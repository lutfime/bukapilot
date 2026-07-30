"""Unit tests for video HTTP server and hotspot helpers."""

import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from openpilot.selfdrive.appbridged.video_hotspot import compute_hotspot_credentials, get_hotspot_ip
from openpilot.selfdrive.appbridged.video_http_server import VideoHttpServer


class TestVideoHotspot(unittest.TestCase):
  def test_get_hotspot_ip_prefers_os_configured_address(self):
    with patch("openpilot.selfdrive.appbridged.video_hotspot._read_configured_hotspot_ip", return_value="192.168.69.1"):
      with patch("openpilot.selfdrive.appbridged.video_hotspot._iface_ipv4_addresses", return_value=["10.42.0.1", "192.168.69.1"]):
        self.assertEqual(get_hotspot_ip(), "192.168.69.1")

  def test_get_hotspot_ip_uses_configured_before_iface_is_up(self):
    with patch("openpilot.selfdrive.appbridged.video_hotspot._read_configured_hotspot_ip", return_value="10.10.10.1"):
      with patch("openpilot.selfdrive.appbridged.video_hotspot._iface_ipv4_addresses", return_value=[]):
        self.assertEqual(get_hotspot_ip(), "10.10.10.1")

  def test_get_hotspot_ip_uses_live_iface_when_unconfigured(self):
    with patch("openpilot.selfdrive.appbridged.video_hotspot._read_configured_hotspot_ip", return_value=None):
      with patch("openpilot.selfdrive.appbridged.video_hotspot._iface_ipv4_addresses", return_value=["172.16.0.1"]):
        self.assertEqual(get_hotspot_ip(), "172.16.0.1")

  def test_get_hotspot_ip_prefers_dnsmasq_subnet_on_iface(self):
    with patch("openpilot.selfdrive.appbridged.video_hotspot._read_configured_hotspot_ip", return_value=None):
      with patch("openpilot.selfdrive.appbridged.video_hotspot._read_dnsmasq_gateway_ip", return_value="192.168.69.1"):
        with patch("openpilot.selfdrive.appbridged.video_hotspot._iface_ipv4_addresses", return_value=["10.42.0.1", "192.168.69.1"]):
          self.assertEqual(get_hotspot_ip(), "192.168.69.1")

  def test_get_hotspot_ip_uses_dnsmasq_when_only_nmcli_on_iface(self):
    with patch("openpilot.selfdrive.appbridged.video_hotspot._read_configured_hotspot_ip", return_value=None):
      with patch("openpilot.selfdrive.appbridged.video_hotspot._read_dnsmasq_gateway_ip", return_value="192.168.69.1"):
        with patch("openpilot.selfdrive.appbridged.video_hotspot._iface_ipv4_addresses", return_value=["10.42.0.1"]):
          self.assertEqual(get_hotspot_ip(), "192.168.69.1")

  def test_get_hotspot_ip_uses_live_when_unconfigured(self):
    with patch("openpilot.selfdrive.appbridged.video_hotspot._read_configured_hotspot_ip", return_value=None):
      with patch("openpilot.selfdrive.appbridged.video_hotspot._read_dnsmasq_gateway_ip", return_value=None):
        with patch("openpilot.selfdrive.appbridged.video_hotspot._iface_ipv4_addresses", return_value=["10.42.0.1"]):
          self.assertEqual(get_hotspot_ip(), "10.42.0.1")

  def test_compute_hotspot_credentials_format(self):
    with patch("openpilot.selfdrive.appbridged.video_hotspot._read_cpu_serial", return_value="abc"):
      with patch("openpilot.selfdrive.appbridged.video_hotspot._read_wlan0_mac", return_value="aa:bb:cc:dd:ee:ff"):
        ssid, password = compute_hotspot_credentials()
    self.assertTrue(ssid.startswith("KommuAssist_"))
    self.assertEqual(len(password), 16)


class TestVideoHttpServer(unittest.TestCase):
  def test_health_endpoint(self):
    server = VideoHttpServer(18090)
    self.assertTrue(server.start())
    try:
      import urllib.request

      with urllib.request.urlopen("http://127.0.0.1:18090/health", timeout=5) as resp:
        self.assertEqual(resp.status, 200)
        self.assertEqual(resp.read(), b"ok")
    finally:
      server.stop()

  def test_streams_file_with_token(self):
    completed = threading.Event()
    sizes = []

    with tempfile.TemporaryDirectory() as tmp:
      mp4 = Path(tmp) / "clip.mp4"
      mp4.write_bytes(b"test-mp4-bytes")
      server = VideoHttpServer(18089)
      server.on_complete = lambda tid, size: (sizes.append(size), completed.set())
      self.assertTrue(server.start())
      try:
        token = "secret-token"
        server.register(7, token, mp4, time.monotonic() + 30)
        import urllib.request

        url = f"http://127.0.0.1:18089/video/download/7?token={token}"
        with urllib.request.urlopen(url, timeout=5) as resp:
          body = resp.read()
        self.assertEqual(body, b"test-mp4-bytes")
        self.assertTrue(completed.wait(2))
        self.assertEqual(sizes, [len(body)])
      finally:
        server.stop()


if __name__ == "__main__":
  unittest.main()
