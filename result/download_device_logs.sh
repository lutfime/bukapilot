#!/usr/bin/env bash
# ===================================================================
# Download Kommu device logs to THIS Mac.
#
# HOW TO USE (in the car):
#   1. Connect this Mac to the DEVICE's WiFi network (the comma's own hotspot).
#      (You won't have internet — that's fine, this script doesn't need it.)
#   2. Run:   bash result/download_device_logs.sh
#   3. Everything saves under result/device-logs-<timestamp>/ on this Mac.
#   4. Switch the Mac back to internet WiFi and tell Claude.
#
# Optional: pass the device IP if it's not found auto:  bash result/download_device_logs.sh 192.168.0.1
# Works the same at home (it auto-tries 192.168.0.9).
# ===================================================================
set -uo pipefail

USER_H="kommu"
CANDIDATES=("${1:-}" 192.168.0.1 192.168.69.1)
SSHC="ssh -o ConnectTimeout=5 -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR"

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

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUT="$SCRIPT_DIR/device-logs-$(date +%Y%m%d-%H%M%S)"
mkdir -p "$OUT/logs" "$OUT/drive" "$OUT/params"
echo ">> Saving to: $OUT"
echo ""

echo ">> [1/5] Deploying diag script to device..."
scp -o ConnectTimeout=5 -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
    "$SCRIPT_DIR/grab_diag.py" "$DEV:/data/grab_diag.py" 2>/dev/null && echo "   ok" || echo "   (scp failed, will use inline fallback)"

echo ">> [2/5] Running diag on device..."
if $SSHC "$DEV" 'test -f /data/grab_diag.py' 2>/dev/null; then
  $SSHC "$DEV" '/usr/local/venv/bin/python /data/grab_diag.py 2>&1' | tee "$OUT/diag.txt" | tail -40
else
  { echo "=== SEL ==="; $SSHC "$DEV" 'cat /data/params/SelectedDrivingModel 2>/dev/null';
    echo; echo "=== PROCS ==="; $SSHC "$DEV" 'pgrep -af "modeld|controlsd" | grep -v pgrep';
    echo; echo "=== MODELD SELECTION+STARTUP ==="; $SSHC "$DEV" 'grep -ah "\"filename\": \"modeld.py\"" /data/log/swaglog.* | grep -aE "selection|using RKNN|models loaded|fall|modeld init|tinygrad" | tail -15';
    echo; echo "=== MODELD ERRORS ==="; $SSHC "$DEV" 'grep -ah "\"filename\": \"modeld.py\"" /data/log/swaglog.* | grep -aiE "error|traceback|exc_info" | tail -12'; } | tee "$OUT/diag.txt"
fi
echo "   (full diag -> $OUT/diag.txt)"
echo ""

echo ">> [3/5] Downloading swaglog (/data/log/, all crash/app logs)..."
rsync -az -e "$SSHC" "$DEV:/data/log/" "$OUT/logs/" 2>/dev/null && echo "   ok ($(du -sh "$OUT/logs" 2>/dev/null | cut -f1))" || echo "   (rsync failed)"
echo ""

echo ">> [4/5] Downloading latest drive (qlog + rlog)..."
LATEST=$($SSHC "$DEV" 'ls -dt /data/media/0/realdata/2026-*--* 2>/dev/null | head -1')
if [ -n "$LATEST" ]; then
  echo "   latest segment: $(basename "$LATEST")"
  # the route = strip the trailing --N
  ROUTE_PREFIX=$(echo "$LATEST" | sed -E 's/--[0-9]+$//')
  # pull qlog+rlog from the last 4 segments of this route
  for seg in $($SSHC "$DEV" "ls -d ${ROUTE_PREFIX}--* 2>/dev/null | tail -4"); do
    n=$(basename "$seg")
    scp -o ConnectTimeout=5 -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
        "$DEV:$seg/qlog.zst" "$OUT/drive/${n}--qlog.zst" 2>/dev/null && echo "   $n qlog ok"
    scp -o ConnectTimeout=5 -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
        "$DEV:$seg/rlog.zst" "$OUT/drive/${n}--rlog.zst" 2>/dev/null && echo "   $n rlog ok"
  done
else
  echo "   (no route found)"
fi
echo ""

echo ">> [5/5] Grabbing param/toggle files..."
for pf in SelectedDrivingModel UseSupercomboModel X70UsePidController MadsEnabled CSCEnabled MapCornerEnabled; do
  scp -o ConnectTimeout=5 -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
      "$DEV:/data/params/$pf" "$OUT/params/$pf" 2>/dev/null && echo "   $pf = $(cat "$OUT/params/$pf" 2>/dev/null)"
done
echo ""

echo "========================================================"
echo "DONE. All logs are in: $OUT"
echo "Tell Claude you're back online; the diag is in $OUT/diag.txt"
echo "========================================================"
