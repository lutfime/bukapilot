#!/usr/bin/env bash
# ===================================================================
# Deploy EXPANDED MADS_SIG diagnostics to find the real "standby" signal.
#
# WHY:
#   CRUISE_AVAILABLE (cam bus 2) is UNSTABLE — it drops for 1-5 seconds during a
#   sustained MAIN press. Tracking it makes MADS flap (engage/disengage). Debouncing
#   a multi-second drop is dangerous (masks real disengagement), so we reverted that.
#   Instead we log ALL candidate signals to find which one is stable when the HUD
#   shows "standby".
#
# NEW SIGNALS LOGGED IN MADS_SIG:
#   cruise_avail_pt     = CRUISE_AVAILABLE (PCM_BUTTONS, bus 0)  <- NEW: powertrain, may be cleaner
#   cruise_avail_cam    = CRUISE_AVAILABLE (PCM_BUTTONS, bus 2)  <- old: noisy (1-3s drops)
#   cruise_ctrl_en      = CRUISE_CONTROL_EN  (GAS_PEDAL, bus 0)
#   driving_modes_pt    = DRIVING_MODES 2-bit (PCM_BUTTONS, bus 0)
#   driving_modes_cam   = DRIVING_MODES 2-bit (PCM_BUTTONS, bus 2)
#   cruise_btn          = CRUISE_BTN (ACC_BUTTONS, bus 0)
#   cruise_disabled_cam= CRUISE_DISABLED (ACC_CMD, bus 2)
#   motion_control_cam  = MOTION_CONTROL 4-bit (ACC_CMD, bus 2)
#   (plus: acc_on_off_pt, acc_on_off_cam, gas_override, is_icc_on)
#
# NOTE: This is DIAGNOSTIC ONLY. The available=cruise_avail_cam logic is unchanged,
#   so MADS will still flap. That's expected — we need the data first. After this
#   drive we'll switch `available` to the stable signal and the flapping stops.
#
# HOW TO USE:
#   1. Connect Mac to device WiFi. Run: bash result/deploy_mads_sig_diag.sh
#   2. Drive >20 km/h. Do this sequence while watching the HUD:
#        a. MAIN NOT pressed -> note HUD shows nothing        (drive ~10s)
#        b. Press MAIN, HOLD -> note HUD shows "standby"     (hold ~10s)
#        c. Press SET -> note HUD shows cruise active        (hold ~5s)
#        d. Cancel (or brake) -> HUD back to standby/off
#   3. Run: bash result/download_device_logs.sh and tell Claude.
#   Tell Claude which HUD state matched which signal value. We'll pick the stable one.
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

echo ">> [1/3] Backing up current carstate.py on device..."
$SSHC "$DEV" 'cp /data/openpilot/opendbc_repo/opendbc/car/proton/carstate.py /data/openpilot/opendbc_repo/opendbc/car/proton/carstate.py.bak.$(date +%s) 2>/dev/null; echo ok'

echo ">> [2/3] Deploying expanded-diagnostic carstate.py to BOTH overlay locations..."
for LOC in /data/openpilot /data/safe_staging/merged; do
  $SCPC "$REPO/opendbc_repo/opendbc/car/proton/carstate.py" "$DEV:$LOC/opendbc_repo/opendbc/car/proton/carstate.py" && echo "   carstate.py -> $LOC"
done

echo ">> [3/3] Verifying + restarting..."
$SSHC "$DEV" 'grep -c "cruise_ctrl_en\|driving_modes_pt\|cruise_btn\|cruise_disabled_cam\|motion_control_cam" /data/openpilot/opendbc_repo/opendbc/car/proton/carstate.py'
$SSHC "$DEV" '/usr/local/venv/bin/python -c "import ast; ast.parse(open(\"/data/openpilot/opendbc_repo/opendbc/car/proton/carstate.py\").read()); print(\"   syntax OK\")"'
$SSHC "$DEV" 'sudo systemctl restart kommu.service' 2>/dev/null && echo "   restarted via systemctl" || echo "   !! POWER-CYCLE the device instead."
sleep 8
$SSHC "$DEV" 'pgrep -af "selfdrive.car.card" | grep -v pgrep | head -1 || echo "   card NOT running"'

echo ""
echo "========================================================"
echo "DONE. Expanded MADS_SIG diagnostics deployed."
echo ""
echo "DRIVE TEST (watch the HUD carefully):"
echo "  a. MAIN OFF, drive ~10s  -> HUD: nothing"
echo "  b. Press+HOLD MAIN ~10s  -> HUD: 'standby' (cruise armed)"
echo "  c. Press SET ~5s         -> HUD: cruise active (set speed)"
echo "  d. Cancel/brake         -> HUD: back to standby or off"
echo ""
echo "Then run: bash result/download_device_logs.sh"
echo "Tell Claude what the HUD showed at each step. We'll match it"
echo "to the MADS_SIG signal values and pick the stable 'standby' signal."
echo "========================================================"
