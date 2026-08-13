#!/usr/bin/env bash
# ===================================================================
# Deploy the MADS standby debounce FIX (stop cruise_avail glitch flapping).
#
# PROBLEM (from 20260813-161722 logs):
#   CRUISE_AVAILABLE (cam bus 2, the "MAIN armed" signal) glitches False for
#   1-2 frames during a sustained MAIN press. Each glitch drops cruiseState.available
#   -> lat_only=False -> the gas gate `gas_disengage and not lat_only` fires
#   -> pedalPressed event -> DISENGAGE. Next frame available returns -> re-engage.
#   This rapid disengage/re-engage cycle is the intermittent "gas still working /
#   jerky" behavior in standby. MADS_DEBUG showed it firing repeatedly:
#     frame=11450: lat_only=True  cruise_avail=True
#     frame=11500: lat_only=False cruise_avail=False  <- glitch disengage
#     frame=11550: lat_only=False cruise_avail=False
#     frame=11600: lat_only=True  cruise_avail=True   <- re-engage
#
# FIX:
#   carstate.py: debounce CRUISE_AVAILABLE on the falling edge. While the raw
#   signal is True, available=True immediately (no rising-edge delay). When it
#   drops, hold available True for 30 frames (~0.3s) so 1-2 frame CAN glitches
#   don't drop it. Brake still disengages instantly (separate path, not gated
#   by lat_only). Intentional MAIN release adds ~0.3s latency — smooth and safe.
#
# HOW TO USE:
#   1. Connect Mac to device WiFi.
#   2. Run:   bash result/deploy_mads_debounce.sh
#   3. Drive >20 km/h, press MAIN (hold it), press gas — should stay GREEN, no
#      disengage. If still flapping, run download_device_logs.sh and tell Claude.
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

echo ">> [1/4] Backing up current carstate.py on device..."
$SSHC "$DEV" 'cp /data/openpilot/opendbc_repo/opendbc/car/proton/carstate.py /data/openpilot/opendbc_repo/opendbc/car/proton/carstate.py.bak.$(date +%s) 2>/dev/null; echo ok'

echo ">> [2/4] Deploying fixed carstate.py to BOTH overlay locations..."
for LOC in /data/openpilot /data/safe_staging/merged; do
  $SCPC "$REPO/opendbc_repo/opendbc/car/proton/carstate.py" "$DEV:$LOC/opendbc_repo/opendbc/car/proton/carstate.py" && echo "   carstate.py -> $LOC"
done

echo ">> [3/4] Verifying fix..."
$SSHC "$DEV" 'grep -n "_cruise_avail_hold" /data/openpilot/opendbc_repo/opendbc/car/proton/carstate.py'
$SSHC "$DEV" '/usr/local/venv/bin/python -c "import ast; ast.parse(open(\"/data/openpilot/opendbc_repo/opendbc/car/proton/carstate.py\").read()); print(\"   syntax OK\")"'

echo ">> [4/4] Restarting kommu.service..."
$SSHC "$DEV" 'sudo systemctl restart kommu.service' 2>/dev/null && echo "   restarted via systemctl" || {
  echo "   !! systemctl restart failed. POWER-CYCLE the device instead. The fix is on disk."
}
sleep 8

echo ""
echo ">> card process:"
$SSHC "$DEV" 'pgrep -af "selfdrive.car.card" | grep -v pgrep | head -1 || echo "   NOT running (wait 5s)"'

echo ""
echo "========================================================"
echo "DONE. MADS debounce fix deployed."
echo ""
echo "Test:"
echo "  1. Drive >20 km/h, press and HOLD MAIN -> GREEN (standby)."
echo "  2. While holding MAIN, press gas — should STAY GREEN (no disengage)."
echo "  3. Release MAIN -> ~0.3s later, disengages (smooth)."
echo "  4. Brake -> instant disengage (always)."
echo "  5. If still flapping, run bash result/download_device_logs.sh"
echo "     and tell Claude. MADS_DEBUG will show if cruise_avail still drops."
echo "========================================================"
