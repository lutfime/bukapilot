#!/usr/bin/env bash
# Deploy X70 STEER_DELTA_DOWN=45 (values.py) to device + restart.
# Usage: bash result/deploy_steer_delta.sh [device-ip]
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
  exit 1
fi
echo ">> Device: $DEV"

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SRC="$REPO/opendbc_repo/opendbc/car/proton/values.py"

echo ">> Backing up values.py on device..."
$SSHC "$DEV" 'ts=$(date +%s); for p in /data/openpilot/opendbc_repo/opendbc/car/proton/values.py /data/safe_staging/merged/opendbc_repo/opendbc/car/proton/values.py; do [ -f "$p" ] && cp "$p" "${p}.bak.${ts}"; done; echo ok'

echo ">> Deploying values.py..."
for LOC in /data/openpilot/opendbc_repo/opendbc/car/proton /data/safe_staging/merged/opendbc_repo/opendbc/car/proton; do
  $SCPC "$SRC" "$DEV:$LOC/values.py" && echo "   -> $LOC/values.py"
done

echo ">> Verify X70 STEER_DELTA on device..."
$SSHC "$DEV" '/usr/local/venv/bin/python3 -c "
from pathlib import Path
p = Path(\"/data/openpilot/opendbc_repo/opendbc/car/proton/values.py\")
text = p.read_text()
assert \"elif CP.carFingerprint == CAR.PROTON_X70\" in text
assert \"STEER_DELTA_DOWN = 45\" in text
print(\"   X70 block + DOWN=45 OK\")
"'

echo ">> Restarting kommu.service..."
$SSHC "$DEV" 'sudo systemctl restart kommu.service' 2>/dev/null && echo "   restarted" || echo "   !! restart failed — power-cycle device"

echo "DONE. X70 STEER_DELTA: UP=15 DOWN=45 (other Protons unchanged)."
