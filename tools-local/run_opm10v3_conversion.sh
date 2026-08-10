#!/bin/bash
# run_opm10v3_conversion.sh — Convert OP Model 10 V3 (3-file split) to RKNN
# inside an Ubuntu container. This is the one command to run.
#
# Pre-reqs:
#   brew install colima docker   (if not already installed)
#   colima start                 (start the Docker VM, ~30s)
#
# Then: ./tools-local/run_opm10v3_conversion.sh
#
# The ONNX files must be at /tmp/sp-models/opm10v3-onnx/ on this machine.
# They will be copied into the repo's selfdrive/modeld/models/ dir before conversion.
set -e

cd "$(git rev-parse --show-toplevel)"

ONNX_SRC="/tmp/sp-models/opm10v3-onnx"
MODELS_DIR="selfdrive/modeld/models"

# --- Copy ONNX files into the repo if not already there ---
echo "=== checking ONNX source files ==="
for f in driving_vision.onnx driving_on_policy.onnx driving_off_policy.onnx; do
  dest="$MODELS_DIR/$f"
  # Check if already in place and is a real ONNX (not an LFS pointer)
  if [ -f "$dest" ] && [ $(wc -c < "$dest") -gt 10000 ]; then
    echo "  [ok] $f already in place ($(du -h "$dest" | cut -f1))"
  elif [ -f "$ONNX_SRC/$f" ]; then
    cp "$ONNX_SRC/$f" "$dest"
    echo "  [copy] $ONNX_SRC/$f -> $dest ($(du -h "$dest" | cut -f1))"
  else
    echo "  [FAIL] $f not found at $ONNX_SRC/$f"
    echo "         Download it first (see MODEL-CONVERSION-GUIDE.md or the OPM10V3 investigation)"
    exit 1
  fi
done

echo ""
echo "=== starting OPM10V3 RKNN conversion in Ubuntu container ==="
echo ""

# Mount the repo at /work, run the conversion script in Ubuntu 22.04 with Python 3.10.
docker run --rm \
  -v "$PWD":/work \
  -w /work \
  -e REPO_ROOT_OVERRIDE=/work \
  ubuntu:22.04 \
  bash -c '
    set -e
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -qq && apt-get install -y -qq python3 python3-pip wget curl git libgl1 >/dev/null
    # Install RKNN-Toolkit2 (Ubuntu 22.04 / Python 3.10 wheel from airockchip)
    pip3 install -q --no-input \
      "rknn-toolkit2 @ https://github.com/airockchip/rknn-toolkit2/raw/master/rknn-toolkit2/packages/ubuntu22.04/x86_64/rknn_toolkit2-2.x.x-cp310-cp310-manylinux_2_17_x86_64.manylinux2014_x86_64.whl" \
      2>&1 | tail -5 || (
        echo "[fallback] wheel URL pattern may have changed; installing from PyPI mirror..."
        pip3 install -q --no-input rknn-toolkit2 2>&1 | tail -5
      )
    pip3 install -q --no-input onnx numpy
    echo "--- running conversion ---"
    python3 tools-local/convert_opm10v3_to_rknn.py
  '

echo ""
echo "=== result ==="
ls -lh \
  "$MODELS_DIR"/driving_vision_opm10v3.rknn \
  "$MODELS_DIR"/driving_vision_opm10v3_metadata.pkl \
  "$MODELS_DIR"/driving_on_policy_opm10v3.rknn \
  "$MODELS_DIR"/driving_on_policy_opm10v3_metadata.pkl \
  "$MODELS_DIR"/driving_off_policy_opm10v3.rknn \
  "$MODELS_DIR"/driving_off_policy_opm10v3_metadata.pkl \
  2>&1
