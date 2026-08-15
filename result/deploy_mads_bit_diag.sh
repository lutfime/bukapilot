#!/usr/bin/env bash
# ===================================================================
# Deploy MADS_BIT diagnostic (NO control-path change).
#
# Logs every ~1s when MADS is on:
#   b16 = KA2 ACC_ON_OFF_BUTTON (bit 16)  — KA1 may have called this ACC_SET
#   b17 = KA2 CRUISE_AVAILABLE  (bit 17)  — KA1 may have called this ACC_ON_OFF_BUTTON
#   _pt = powertrain bus 0, _cam = camera bus 2
#   UNSEEN(v=0.0) = no frame received on that bus (ts_nanos==0)
#   NAN             = parser value is NaN (should be rare; defaults are 0.0)
#   0/1             = real decoded bit from a received frame
# Also logs gasPressed (APPS_1) so we can correlate drops with the pedal.
#
# Control logic is UNCHANGED (available still = cruise_avail_cam when MADS on).
#
# HOW TO USE:
#   1. Connect Mac to device WiFi. Run: bash result/deploy_mads_bit_diag.sh
#   2. Drive: MAIN off ~10s, HOLD MAIN ~10s, gas while MAIN held, SET, cancel.
#   3. Run: bash result/download_device_logs.sh and tell Claude.
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

echo ">> [1/3] Backing up carstate.py..."
$SSHC "$DEV" 'cp /data/openpilot/opendbc_repo/opendbc/car/proton/carstate.py /data/openpilot/opendbc_repo/opendbc/car/proton/carstate.py.bak.$(date +%s) 2>/dev/null; echo ok'

echo ">> [2/3] Deploying carstate.py (diag only) to BOTH overlay locations..."
for LOC in /data/openpilot /data/safe_staging/merged; do
  $SCPC "$REPO/opendbc_repo/opendbc/car/proton/carstate.py" "$DEV:$LOC/opendbc_repo/opendbc/car/proton/carstate.py" && echo "   carstate.py -> $LOC"
done

echo ">> [3/3] Verify + restart..."
$SSHC "$DEV" 'grep -n "MADS_BIT:" /data/openpilot/opendbc_repo/opendbc/car/proton/carstate.py'
$SSHC "$DEV" '/usr/local/venv/bin/python -c "import ast; ast.parse(open(\"/data/openpilot/opendbc_repo/opendbc/car/proton/carstate.py\").read()); print(\"   syntax OK\")"'
$SSHC "$DEV" 'sudo systemctl restart kommu.service' 2>/dev/null && echo "   restarted via systemctl" || echo "   !! POWER-CYCLE the device instead."
sleep 8
$SSHC "$DEV" 'pgrep -af "selfdrive.car.card" | grep -v pgrep | head -1 || echo "   card NOT running"'

echo ""
echo "========================================================"
echo "DONE. MADS_BIT diagnostic deployed (control path unchanged)."
echo "Drive MAIN off / HOLD MAIN / gas / SET / cancel, then:"
echo "  bash result/download_device_logs.sh"
echo "Look for MADS_BIT lines. We want which of b16/b17 on pt vs cam"
echo "is 1 only when MAIN is armed, and UNSEEN vs real 0/1 on bus 0."
echo "========================================================"
