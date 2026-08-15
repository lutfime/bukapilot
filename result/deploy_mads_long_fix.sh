#!/usr/bin/env bash
# ===================================================================
# Deploy the MADS standby gas-release fix to the device + restart.
#
# BUG: In MADS standby (MAIN armed, no SET), gas was still running intermittently.
# Root cause: the longActive gate used CC.latActive (noisy — standstill/steerFault
# blips drop latActive False for a frame → gate doesn't fire → longActive stays True
# → gas command sent for that frame). Fixed: gate on CC.enabled (stable) instead.
#
# HOW TO USE:
#   1. Connect this Mac to the DEVICE's WiFi (or pass IP as $1).
#   2. Run:   bash result/deploy_mads_long_fix.sh
#   3. When it says DONE, press MAIN to test standby (gas should be manual).
# ===================================================================
set -uo pipefail

USER_H="kommu"
CANDIDATES=("${1:-}" 192.168.69.1 192.168.0.9 192.168.0.1)
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

echo ">> [1/2] Deploying fixed controlsd.py to BOTH overlays..."
for LOC in /data/openpilot /data/safe_staging/merged; do
  $SCPC "$REPO/selfdrive/controls/controlsd.py" "$DEV:$LOC/selfdrive/controls/" && echo "   controlsd.py -> $LOC"
done

echo ">> [2/2] Verifying + restarting..."
$SSHC "$DEV" 'grep -n "gate on CC.enabled" /data/openpilot/selfdrive/controls/controlsd.py && echo "   fix verified" || echo "   !! fix NOT found"
echo "   SelectedDrivingModel: $(cat /data/params/SelectedDrivingModel)"
sudo systemctl restart kommu.service && echo "   restarted"'

echo ""
echo "========================================================"
echo "DONE. MADS standby gas-release fix deployed."
echo "Press MAIN (no SET) to test: gas should be MANUAL now."
echo "If gas still runs, download logs and tell Claude."
echo "========================================================"
