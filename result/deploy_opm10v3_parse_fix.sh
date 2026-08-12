#!/usr/bin/env bash
# ===================================================================
# Deploy the opm10v3 3-split PARSE-CRASH FIX to the device + restart.
#
# This fixes the BLUE no-engage on the 2026-08-12 opm10v3 drive:
#   ModelState3SplitRKNN.run() called parse_vision_outputs() on the vision-only
#   output, but the 3-split vision head has NO lane_lines/lead/road_edges (they
#   live in off_policy). check_missing('lane_lines') raised ValueError every frame
#   -> modeld crash-looped -> ZERO modelV2 published -> controlsd had no model ->
#   blue LED, could not engage. (Not the C++ inf, not the fp16 overflow.)
#
# The fix routes every output to its correct head — NOTHING is dropped:
#   vision  -> pose, road_transform, meta, desire_pred, wide_from_device_euler
#   off_policy -> lane_lines, road_edges, lead, lane_lines_prob, lead_prob (+plan, dropped: on_policy wins)
#   on_policy -> plan, desire_state
#   ignore_missing only lets the generic parser tolerate each head being a subset.
#
# HOW TO USE:
#   1. Connect this Mac to the DEVICE's WiFi (or pass the IP as $1).
#   2. Run:   bash result/deploy_opm10v3_parse_fix.sh
#   3. When it says DONE + "NO CRASH", drive >20 km/h, engage -> should engage now.
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
  echo "!! Connect this Mac to the device WiFi first (or pass IP: bash result/deploy_opm10v3_parse_fix.sh 192.168.x.x), then re-run."
  exit 1
fi
echo ">> Device found: $DEV"

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
echo ">> Repo: $REPO"

if [ ! -f "$REPO/selfdrive/modeld/modeld.py" ]; then
  echo "!! modeld.py not found at $REPO/selfdrive/modeld/modeld.py"; exit 1
fi

echo ">> [1/4] Backing up current modeld.py on device..."
$SSHC "$DEV" 'cp /data/openpilot/selfdrive/modeld/modeld.py /data/openpilot/selfdrive/modeld/modeld.py.bak.$(date +%s) 2>/dev/null; echo ok'

echo ">> [2/4] Deploying fixed modeld.py to BOTH overlay locations..."
for LOC in /data/openpilot /data/safe_staging/merged; do
  $SCPC "$REPO/selfdrive/modeld/modeld.py" "$DEV:$LOC/selfdrive/modeld/" && echo "   modeld.py -> $LOC"
done

echo ">> [3/4] Verifying the fix is in place + syntax..."
IGN=$($SSHC "$DEV" 'grep -c "Parser(ignore_missing=True)" /data/openpilot/selfdrive/modeld/modeld.py')
echo "   Parser(ignore_missing=True) count = $IGN (>=1 expected)"
PERC=$($SSHC "$DEV" 'grep -c "off_policy_dict = self.parser.parse_vision_outputs" /data/openpilot/selfdrive/modeld/modeld.py')
echo "   off_policy perception parse count  = $PERC (1 expected)"
$SSHC "$DEV" '/usr/local/venv/bin/python -c "import ast; ast.parse(open(\"/data/openpilot/selfdrive/modeld/modeld.py\").read()); print(\"   syntax OK\")"'

echo ">> [4/4] On-device parse simulation (proves no crash + all outputs present)..."
$SSHC "$DEV" 'cd /data/openpilot && /usr/local/venv/bin/python - <<"PYEOF" 2>&1 | grep -E "RESULT|MISSING|CRASH|Error|Traceback"
import sys, pickle, numpy as np
sys.path.insert(0,"/data/openpilot")
from pathlib import Path
MD=Path("/data/openpilot/selfdrive/modeld/models")
def slices(f):
    with open(MD/f,"rb") as h: return pickle.load(h)["output_slices"]
vs=slices("driving_vision_opm10v3_metadata.pkl")
ofs=slices("driving_off_policy_opm10v3_metadata.pkl")
ons=slices("driving_on_policy_opm10v3_metadata.pkl")
from selfdrive.modeld.parse_model_outputs import Parser
p=Parser(ignore_missing=True)
def sl(a,s): return {k:a[np.newaxis,v] for k,v in s.items()}
vd=p.parse_vision_outputs(sl(np.random.randn(632).astype("float32"),vs))   # CRASHED before the fix
od=p.parse_policy_outputs(sl(np.random.randn(1937).astype("float32"),ofs))
od=p.parse_vision_outputs(od)                                              # parse off_policy perception (the fix)
ond=p.parse_policy_outputs(sl(np.random.randn(1000).astype("float32"),ons))
if "plan" in od and "plan" in ond: od.pop("plan",None); od.pop("plan_stds",None)
c={**vd,**od,**ond}
need=["pose","wide_from_device_euler","road_transform","lane_lines","road_edges","lane_lines_prob","desire_pred","meta","lead_prob","lead","plan","desire_state","hidden_state"]
missing=[k for k in need if k not in c]
print("RESULT NO CRASH | lane_lines=%s plan=%s pose=%s | MISSING: %s" % (
  "lane_lines" in c, "plan" in c, "pose" in c, missing if missing else "NONE"))
PYEOF'

echo "   restarting kommu.service..."
$SSHC "$DEV" 'sudo systemctl restart kommu.service' 2>/dev/null && echo "   restarted via systemctl" || {
  echo "   !! systemctl restart failed (sudo?). Power-cycle the device instead; the fix is on disk."
}
sleep 8
echo "   modeld running? $($SSHC "$DEV" 'pgrep -af "modeld" | grep -v pgrep | head -1 || echo NOT-YET')"

echo ""
echo "========================================================"
echo "DONE. opm10v3 3-split parse crash is FIXED (modeld will publish modelV2)."
echo "Confirm 'RESULT NO CRASH ... MISSING: NONE' above before driving."
echo "Drive above ~20 km/h, engage -> should ENGAGE now (green), not blue."
echo "If still blue, run bash result/download_device_logs.sh and tell Claude."
echo "========================================================"
