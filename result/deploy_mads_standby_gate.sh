#!/usr/bin/env bash
# ===================================================================
# Deploy the MADS standby-gate FIX (auto-steer only when MAIN armed).
#
# PROBLEM (from 20260813-160608 logs):
#   acc_on_off_pt (ACC_ON_OFF_BUTTON, bus 0) is ALWAYS True on the X70 — it's a
#   latched status, NOT the MAIN button. So the old logic:
#     nonAdaptive = not (acc_on_off or gas_override) = not (True or False) = False
#   made lat_only=True the moment you shifted to DRIVE -> auto-steer engaged
#   WITHOUT pressing MAIN.
#
# FIX:
#   1. carstate.py: ret.cruiseState.available = cruise_avail_cam when MADS on.
#      CRUISE_AVAILABLE (cam bus 2) is the real "MAIN armed" signal (logs show it
#      True for ~4s during a MAIN press, False otherwise).
#   2. carstate.py: revert nonAdaptive to False (remove broken acc_on_off_pt gate).
#   3. selfdrived.py: lat_only = cruiseState.available AND not enabled AND drive.
#      Gates standby lateral on the real MAIN signal instead of nonAdaptive.
#
# RESULT:
#   MAIN off -> available=False -> wrongCarMode (no engage), no auto-steer.
#   MAIN on, no SET -> available=True, enabled=False -> lat_only=True -> GREEN (standby).
#   MAIN on + SET -> enabled=True -> lat_only=False -> normal full ACC.
#
# HOW TO USE:
#   1. Connect Mac to device WiFi.
#   2. Run:   bash result/deploy_mads_standby_gate.sh
#   3. Drive >20 km/h. Press MAIN -> GREEN (standby lateral). Press SET -> full ACC.
#      MAIN off -> no auto-steer. Run download_device_logs.sh and tell Claude if issues.
# ===================================================================
set -uo pipefail

USER_H="kommu"
CANDIDATES=("${1:-}" 192.168.0.1 192.168.69.1 192.168.0.9)
SSHC="ssh -o ConnectTimeout=5 -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR"
SCPC="scp -o ConnectTimeout=5 -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR"

DEV=""
for ip in "${CANDIDATES[@]}"; do
  [ -z "$ip" ] && continue
  if $SSHC "$USER_H@$ip" true 2>/dev/null; then DEV="$USER_H@$ip"; break; fi
done
if [ -z "$DEV" ]; then
  echo "!! Cannot reach device. Tried: ${CANDIDATES[*]}"
  echo "!! Connect this Mac to the device WiFi first, then re-run."
  exit 1
fi
echo ">> Device found: $DEV"

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
echo ">> Repo: $REPO"

echo ">> [1/4] Backing up current files on device..."
$SSHC "$DEV" 'cp /data/openpilot/opendbc_repo/opendbc/car/proton/carstate.py /data/openpilot/opendbc_repo/opendbc/car/proton/carstate.py.bak.$(date +%s) 2>/dev/null; \
                cp /data/openpilot/selfdrive/selfdrived/selfdrived.py /data/openpilot/selfdrive/selfdrived/selfdrived.py.bak.$(date +%s) 2>/dev/null; echo ok'

echo ">> [2/4] Deploying fixed files to BOTH overlay locations..."
for LOC in /data/openpilot /data/safe_staging/merged; do
  $SCPC "$REPO/opendbc_repo/opendbc/car/proton/carstate.py" "$DEV:$LOC/opendbc_repo/opendbc/car/proton/carstate.py" && echo "   carstate.py   -> $LOC"
  $SCPC "$REPO/selfdrive/selfdrived/selfdrived.py"         "$DEV:$LOC/selfdrive/selfdrived/"                    && echo "   selfdrived.py -> $LOC"
done

echo ">> [3/4] Verifying fix..."
$SSHC "$DEV" 'grep -n "cruise_avail_cam) if self.mads_enabled" /data/openpilot/opendbc_repo/opendbc/car/proton/carstate.py'
$SSHC "$DEV" 'grep -n "CS.cruiseState.available and not CS.cruiseState.enabled" /data/openpilot/selfdrive/selfdrived/selfdrived.py'
$SSHC "$DEV" '/usr/local/venv/bin/python -c "import ast; ast.parse(open(\"/data/openpilot/opendbc_repo/opendbc/car/proton/carstate.py\").read()); ast.parse(open(\"/data/openpilot/selfdrive/selfdrived/selfdrived.py\").read()); print(\"   syntax OK\")"'

echo ">> [4/4] Restarting kommu.service..."
$SSHC "$DEV" 'sudo systemctl restart kommu.service' 2>/dev/null && echo "   restarted via systemctl" || {
  echo "   !! systemctl restart failed. POWER-CYCLE the device instead. The fix is on disk."
}
sleep 8

echo ""
echo ">> card process:"
$SSHC "$DEV" 'pgrep -af "selfdrive.car.card" | grep -v pgrep | head -1 || echo "   NOT running (wait 5s)"'
echo ">> selfdrived process:"
$SSHC "$DEV" 'pgrep -af selfdrived | grep -v pgrep | head -1 || echo "   NOT running (wait 5s)"'

echo ""
echo "========================================================"
echo "DONE. MADS standby-gate fix deployed."
echo ""
echo "Test:"
echo "  1. Drive >20 km/h, do NOT press MAIN -> NO auto-steer (expected)."
echo "  2. Press MAIN -> GREEN led = standby lateral engaged."
echo "  3. Press SET -> full ACC (normal)."
echo "  4. If flapping or no engage, run bash result/download_device_logs.sh"
echo "     and tell Claude. MADS_DEBUG will show lat_only/cruise_avail."
echo "========================================================"
