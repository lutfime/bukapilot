#!/usr/bin/env bash
# ===================================================================
# Deploy the MADS card-crash FIX to the device + restart.
#
# WHAT THIS FIXES:
#   card.py crashed on the FIRST CarState update with:
#     NameError: name 'cp' is not defined
#       at carstate.py line 95, inside _update_lks_state(self, cp_cam)
#
#   The MADS diagnostic reads (acc_on_off, gas_override) reference `cp`
#   (the powertrain parser), but _update_lks_state only received `cp_cam`.
#   card is not restart_if_crash, so it stayed dead the whole onroad session
#   -> no carState -> no engagement possible -> solid BLUE led, cruise
#   buttons (MAIN/SET) did nothing.
#
#   FIX: pass `cp` into _update_lks_state(self, cp, cp_cam) and update the
#   call site. After this, card stays alive and MADS_SIG diagnostic logging
#   can finally run (so we can identify which CAN signal is the MAIN button).
#
# HOW TO USE:
#   1. Connect this Mac to the DEVICE's WiFi (same as download_device_logs.sh).
#   2. Run:   bash result/deploy_mads_crashfix.sh
#   3. When it says DONE, put the car in DRIVE, drive >20 km/h, press MAIN.
#      - If ACC_ON_OFF_BUTTON on bus 0 works (KA1 pattern) -> GREEN (MADS standby).
#      - If still no engage, just press MAIN a few times while driving, then
#        run bash result/download_device_logs.sh and tell Claude. The MADS_SIG
#        log will now show which signal flips when MAIN is pressed.
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
$SSHC "$DEV" 'cp /data/openpilot/opendbc_repo/opendbc/car/proton/carstate.py \
                /data/openpilot/opendbc_repo/opendbc/car/proton/carstate.py.bak.$(date +%s) 2>/dev/null; echo ok'

echo ">> [2/4] Deploying fixed carstate.py to BOTH overlay locations..."
for LOC in /data/openpilot /data/safe_staging/merged; do
  $SCPC "$REPO/opendbc_repo/opendbc/car/proton/carstate.py" \
        "$DEV:$LOC/opendbc_repo/opendbc/car/proton/carstate.py" && echo "   carstate.py -> $LOC"
done

echo ">> [3/4] Verifying fix..."
# After the fix, _update_lks_state must take TWO args (cp, cp_cam) and the
# call site must pass both. The bug was one arg each.
SIG=$($SSHC "$DEV" 'grep -n "_update_lks_state" /data/openpilot/opendbc_repo/opendbc/car/proton/carstate.py')
echo "   $SIG"
$SSHC "$DEV" '/usr/local/venv/bin/python -c "import ast; ast.parse(open(\"/data/openpilot/opendbc_repo/opendbc/car/proton/carstate.py\").read()); print(\"   syntax OK\")"'

echo ">> [4/4] Restarting kommu.service..."
$SSHC "$DEV" 'sudo systemctl restart kommu.service' 2>/dev/null && echo "   restarted via systemctl" || {
  echo "   !! systemctl restart failed (sudo?). Instead POWER-CYCLE the device:"
  echo "      unplug from car power / hold power button, then reconnect. The fix is on disk."
}
sleep 8

echo ""
echo ">> card process status:"
$SSHC "$DEV" 'pgrep -af "selfdrive.car.card" | grep -v pgrep | head -1 || echo "   card NOT running yet (wait 5s and re-check)"'

echo ""
echo ">> recent card crashes (should be EMPTY after fix):"
$SSHC "$DEV" 'grep -ah "\"daemon\": \"card\".*NameError\|name .cp. is not defined" /data/log/swaglog.* 2>/dev/null | tail -3 || echo "   (none found in old logs — good)"'

echo ""
echo "========================================================"
echo "DONE. MADS card-crash fix deployed."
echo ""
echo "Next steps:"
echo "  1. Put car in DRIVE, drive above ~20 km/h."
echo "  2. Press MAIN cruise button."
echo "     GREEN led  = MADS standby lateral engaged (ACC_ON_OFF_BUTTON works)."
echo "     Still blue = MAIN signal not found on bus 0 yet."
echo "  3. Either way, press MAIN a few times, then run:"
echo "       bash result/download_device_logs.sh"
echo "     and tell Claude. The MADS_SIG log now shows which CAN signal"
echo "     flips when MAIN is pressed (acc_on_off_pt / acc_on_off_cam /"
echo "     cruise_avail_cam)."
echo "========================================================"
