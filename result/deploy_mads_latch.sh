#!/usr/bin/env bash
# ===================================================================
# Deploy the MADS standby LATCH fix (gas no longer disengages in standby).
#
# ROOT CAUSE (confirmed from 20260813-164247 logs):
#   CRUISE_AVAILABLE on cam bus 2 is the ONLY signal that tracks MAIN, but the camera
#   ECU publishes it intermittently — it drops False for 1-3s during a sustained MAIN
#   press. Bus 0's CRUISE_AVAILABLE is always True (useless, like acc_on_off_pt).
#
#   Each drop made cruiseState.available=False -> lat_only=False -> gas rising edge
#   fired pedalPressed -> DISENGAGE. That's why "press gas disables cruise".
#
# FIX (latch — no debounce, no hack):
#   Once lat_only=True (standby engaged), HOLD it through the 1-3s signal noise.
#   Exit standby ONLY when:
#     - stock ACC fully engages (cruiseState.enabled=True -> full ACC)
#     - gear leaves drive
#     - MAIN signal False for >3s continuously (MAIN truly released, not a glitch)
#   Gas never disengages in standby (lat_only stays True -> gas gate skipped).
#   Brake still fires pedalPressed (enabled=False) but lat_only stays True ->
#   pcmEnable re-injects -> OP re-engages after brake release (matches X70
#   "brake returns to standby" behavior).
#
# HOW TO USE:
#   1. Connect Mac to device WiFi. Run: bash result/deploy_mads_latch.sh
#   2. Drive >20 km/h, press MAIN -> GREEN (standby). Press gas -> STAYS GREEN (no disengage).
#      Press SET -> full ACC. Brake -> back to standby. MAIN off >3s -> disengage.
#   3. If issues, run download_device_logs.sh and tell Claude.
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
$SSHC "$DEV" 'cp /data/openpilot/selfdrive/selfdrived/selfdrived.py /data/openpilot/selfdrive/selfdrived/selfdrived.py.bak.$(date +%s) 2>/dev/null; echo ok'

echo ">> [2/4] Deploying fixed selfdrived.py to BOTH overlay locations..."
for LOC in /data/openpilot /data/safe_staging/merged; do
  $SCPC "$REPO/selfdrive/selfdrived/selfdrived.py" "$DEV:$LOC/selfdrive/selfdrived/" && echo "   selfdrived.py -> $LOC"
done

echo ">> [3/4] Verifying fix..."
$SSHC "$DEV" 'grep -n "_lat_only_off_cnt" /data/openpilot/selfdrive/selfdrived/selfdrived.py'
$SSHC "$DEV" '/usr/local/venv/bin/python -c "import ast; ast.parse(open(\"/data/openpilot/selfdrive/selfdrived/selfdrived.py\").read()); print(\"   syntax OK\")"'

echo ">> [4/4] Restarting kommu.service..."
$SSHC "$DEV" 'sudo systemctl restart kommu.service' 2>/dev/null && echo "   restarted via systemctl" || echo "   !! POWER-CYCLE the device instead."
sleep 8
$SSHC "$DEV" 'pgrep -af selfdrived | grep -v pgrep | head -1 || echo "   selfdrived NOT running"'

echo ""
echo "========================================================"
echo "DONE. MADS standby latch fix deployed."
echo ""
echo "Test:"
echo "  1. Drive >20 km/h, press MAIN -> GREEN (standby)."
echo "  2. Press gas -> STAYS GREEN (no disengage)."
echo "  3. Press SET -> full ACC. Brake -> back to standby."
echo "  4. MAIN off >3s -> disengages."
echo "  5. If issues, run bash result/download_device_logs.sh"
echo "     and tell Claude. MADS_DEBUG shows lat_only state."
echo "========================================================"
