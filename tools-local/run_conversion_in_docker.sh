#!/bin/bash
# run_conversion_in_docker.sh — once Colima is running, executes the RKNN conversion
# inside an Ubuntu container. This is the one command to run after `colima start`.
#
# Pre-reqs:
#   brew install colima docker   (already installing)
#   colima start                 (start the Docker VM, ~30s)
#
# Then: ./tools-local/run_conversion_in_docker.sh
set -e

cd "$(git rev-parse --show-toplevel)"

echo "=== starting RKNN conversion in Ubuntu container ==="
echo "model: selfdrive/modeld/models/driving_supercombo.onnx ($(ls -lh selfdrive/modeld/models/driving_supercombo.onnx | awk '{print $5}'))"
echo ""

# Mount the repo at /work, run the conversion script in Ubuntu 22.04 with Python 3.10.
# RKNN-Toolkit2 wheels are published at https://github.com/airockchip/rknn-toolkit2.
docker run --rm \
  -v "$PWD":/work \
  -w /work \
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
    python3 tools-local/convert_011_model_to_rknn.py
  '

echo ""
echo "=== result ==="
ls -lh selfdrive/modeld/models/driving_supercombo.rknn selfdrive/modeld/models/driving_supercombo_metadata.pkl 2>&1
