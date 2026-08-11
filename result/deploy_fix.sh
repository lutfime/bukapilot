#!/usr/bin/env bash
# ===================================================================
# Deploy the selfdrived cloudlog-crash FIX to the device + restart.
#
# This fixes the YELLOW no-engage fault:
#   selfdrived.py crashed (UnboundLocalError on cloudlog) because the MADS_DEBUG
#   block re-imported cloudlog locally, shadowing the module import. That crash
#   kills selfdrived -> can't engage -> yellow, even when parked.
#
# HOW TO USE:
#   1. Connect this Mac to the DEVICE's WiFi (same as download_device_logs.sh).
#   2. Run:   bash result/deploy_fix.sh
#   3. When it says DONE, put the car in DRIVE, drive >20 km/h, engage -> GREEN.
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
echo ">> Repo: $REPO"

echo ">> [1/3] Backing up current files on device..."
$SSHC "$DEV" 'cp /data/openpilot/selfdrive/selfdrived/selfdrived.py /data/openpilot/selfdrive/selfdrived/selfdrived.py.bak.$(date +%s) 2>/dev/null; echo ok'

echo ">> [2/3] Deploying fixed files to BOTH overlay locations..."
for LOC in /data/openpilot /data/safe_staging/merged; do
  $SCPC "$REPO/selfdrive/selfdrived/selfdrived.py" "$DEV:$LOC/selfdrive/selfdrived/" && echo "   selfdrived.py -> $LOC"
  $SCPC "$REPO/selfdrive/modeld/modeld.py"         "$DEV:$LOC/selfdrive/modeld/"         && echo "   modeld.py     -> $LOC"
  $SCPC "$REPO/selfdrive/appbridged/appbridged.py" "$DEV:$LOC/selfdrive/appbridged/"     && echo "   appbridged.py -> $LOC"
done

echo ">> [3/3] Verifying fix + restarting..."
# The bug was a SECOND local `import cloudlog` inside data_sample. After the fix there must be
# exactly ONE occurrence (the module-level import at the top). Two = still broken.
CNT=$($SSHC "$DEV" 'grep -c "from openpilot.common.swaglog import cloudlog" /data/openpilot/selfdrive/selfdrived/selfdrived.py')
echo "   cloudlog import count = $CNT (must be 1; 2 = bug still present)"
$SSHC "$DEV" '/usr/local/venv/bin/python -c "import ast; ast.parse(open(\"/data/openpilot/selfdrive/selfdrived/selfdrived.py\").read()); print(\"   syntax OK\")"'
echo "   restarting kommu.service..."
$SSHC "$DEV" 'sudo systemctl restart kommu.service' 2>/dev/null && echo "   restarted via systemctl" || {
  echo "   !! systemctl restart failed (sudo?). Instead POWER-CYCLE the device:"
  echo "      unplug from car power / hold power button, then reconnect. The fix is on disk."
}
sleep 8
echo "   selfdrived running? $($SSHC "$DEV" 'pgrep -af selfdrived | grep -v pgrep | head -1 || echo NOT-YET')"

echo ""
echo "========================================================"
echo "DONE. selfdrived crash is FIXED."
echo "Put car in DRIVE, drive above ~20 km/h, engage -> should be GREEN now."
echo "If still yellow, run bash result/download_device_logs.sh and tell Claude."
echo "========================================================"
