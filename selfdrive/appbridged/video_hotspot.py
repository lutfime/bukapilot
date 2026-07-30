import time
import glob
import hashlib
import re
import subprocess
import threading

HOTSPOT_SERVICE = "wlan1-setup.service"
HOTSPOT_IFACE = "wlan1"
_hotspot_lock = threading.Lock()
HOTSPOT_NETWORK_CONF = "/etc/systemd/network/80-wlan1.network"
DNSMASQ_CONF = "/etc/dnsmasq.conf"


def _read_cpu_serial() -> str:
  try:
    with open("/proc/cpuinfo") as f:
      for line in f:
        if line.startswith("Serial"):
          return line.split(":", 1)[1].strip()
  except OSError:
    pass
  return ""


def _read_wlan0_mac() -> str:
  try:
    with open("/sys/class/net/wlan0/address") as f:
      return f.read().strip()
  except OSError:
    pass
  return ""


def compute_hotspot_credentials() -> tuple[str, str]:
  serial = _read_cpu_serial()
  mac = _read_wlan0_mac().replace(":", "").replace("-", "")
  imei = hashlib.sha256(mac.encode()).hexdigest()[:15]
  dongleid = hashlib.sha224(f"{imei}{serial}".encode()).hexdigest()[:16]
  ssid = f"KommuAssist_{dongleid}"
  return ssid, dongleid


def _parse_networkd_address(path: str) -> str | None:
  try:
    with open(path) as f:
      for line in f:
        line = line.strip()
        if line.startswith("Address="):
          addr = line.split("=", 1)[1].split("/")[0].strip()
          if addr:
            return addr
  except OSError:
    pass
  return None


def _read_configured_hotspot_ip() -> str | None:
  paths: list[str] = []
  if HOTSPOT_NETWORK_CONF:
    paths.append(HOTSPOT_NETWORK_CONF)
  paths.extend(sorted(glob.glob("/etc/systemd/network/*wlan1*.network")))
  seen: set[str] = set()
  for path in paths:
    if path in seen:
      continue
    seen.add(path)
    if addr := _parse_networkd_address(path):
      return addr
  return _read_dnsmasq_gateway_ip()


def _read_dnsmasq_gateway_ip() -> str | None:
  try:
    with open(DNSMASQ_CONF) as f:
      for line in f:
        match = re.match(r"dhcp-range=(\d+\.\d+\.\d+)\.\d+,", line.strip())
        if match:
          return f"{match.group(1)}.1"
  except OSError:
    pass
  return None


def _iface_ipv4_addresses(iface: str) -> list[str]:
  try:
    out = subprocess.check_output(["ip", "-4", "addr", "show", iface], text=True, timeout=2)
  except (subprocess.SubprocessError, OSError):
    return []
  return re.findall(r"inet (\d+\.\d+\.\d+\.\d+)/", out)




def _hotspot_iface_up() -> bool:
  try:
    with open(f"/sys/class/net/{HOTSPOT_IFACE}/operstate", "r", encoding="utf-8") as f:
      state = f.read().strip()
    return state in ("up", "unknown")
  except OSError:
    return False


def is_hotspot_joinable() -> bool:
  return is_hotspot_active() and _hotspot_iface_up()


def wait_for_hotspot_ready(timeout_sec: float = 20.0, poll_sec: float = 0.4) -> bool:
  deadline = time.monotonic() + max(0.5, timeout_sec)
  while time.monotonic() < deadline:
    if is_hotspot_joinable():
      return True
    time.sleep(poll_sec)
  return is_hotspot_joinable()

def is_hotspot_active() -> bool:
  return bool(_iface_ipv4_addresses(HOTSPOT_IFACE))


def get_hotspot_ip() -> str:
  """Return hotspot gateway IPv4 from systemd network config, dnsmasq, or live wlan1."""
  configured = _read_configured_hotspot_ip()
  dnsmasq = _read_dnsmasq_gateway_ip()
  live = _iface_ipv4_addresses(HOTSPOT_IFACE)

  if configured:
    return configured

  if dnsmasq:
    if dnsmasq in live or not live:
      return dnsmasq
    prefix = dnsmasq.rsplit(".", 1)[0]
    if not any(a.rsplit(".", 1)[0] == prefix for a in live):
      return dnsmasq

  if live:
    if len(live) == 1:
      return live[0]
    if dnsmasq:
      prefix = dnsmasq.rsplit(".", 1)[0]
      for addr in live:
        if addr.rsplit(".", 1)[0] == prefix:
          return addr
    gateways = [a for a in live if a.endswith(".1")]
    if len(gateways) == 1:
      return gateways[0]
    return live[0]

  raise OSError(f"No hotspot IPv4 address available on {HOTSPOT_IFACE}")


def _systemctl(action: str, service: str = HOTSPOT_SERVICE) -> None:
  try:
    subprocess.run(["sudo", "systemctl", action, service], check=True, timeout=30)
  except (subprocess.SubprocessError, OSError):
    pass


def enable_hotspot() -> None:
  def worker():
    with _hotspot_lock:
      _systemctl("start")
  threading.Thread(target=worker, daemon=True).start()


def disable_hotspot() -> None:
  def worker():
    with _hotspot_lock:
      try:
        subprocess.run(["sudo", "ip", "link", "set", HOTSPOT_IFACE, "down"], check=False, timeout=5)
      except (subprocess.SubprocessError, OSError):
        pass
      _systemctl("stop")
  threading.Thread(target=worker, daemon=True).start()
