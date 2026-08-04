#!/usr/bin/env python3
"""
Convert the standard openpilot 0.11 driving model (driving_supercombo.onnx) to RKNN
for the Kommu KA2 (Rockchip RK3588 NPU).

MUST RUN ON UBUNTU (RKNN-Toolkit2 is Ubuntu-only). Run via Docker:
    docker run --rm -v "$PWD":/work -w /work ubuntu:22.04 bash -c "
      apt-get update && apt-get install -y python3.10 python3-pip wget git \\
      && pip3 install rknn-toolkit2 onnx numpy \\
      && python3 tools-local/convert_011_model_to_rknn.py"

Or on a Linux box / VM with RKNN-Toolkit2 installed:
    python3 tools-local/convert_011_model_to_rknn.py

Output: selfdrive/modeld/models/driving_supercombo.rknn (+ metadata pkl)
"""
import os
import sys
import pickle
from pathlib import Path

# RKNN-Toolkit2 imports — will fail clearly if not installed
try:
  from rknn.api import RKNN
except ImportError:
  print("ERROR: rknn-toolkit2 not installed. Ubuntu-only. See docstring.", file=sys.stderr)
  print("  pip install rknn-toolkit2", file=sys.stderr)
  sys.exit(1)

import numpy as np

# --- Configuration ----------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[2]
MODELS_DIR = REPO_ROOT / "selfdrive" / "modeld" / "models"

# Pull the 0.11 model from comma's repo (or place it manually in MODELS_DIR)
ONNX_URL = "https://github.com/commaai/openpilot/raw/master/selfdrive/modeld/models/driving_supercombo.onnx"
ONNX_PATH = MODELS_DIR / "driving_supercombo.onnx"
RKNN_PATH = MODELS_DIR / "driving_supercombo.rknn"
META_PATH = MODELS_DIR / "driving_supercombo_metadata.pkl"

TARGET_PLATFORM = "rk3588"

# FP16 quantization — Kommu's approach (matches their runtime casting inputs to float16).
# INT8 loses too much precision (RKPilot/sunnypilot-pc rejected RKNN for this reason).
QUANTIZE_FP16 = True

# NPU core mask — Kommu pins driving to core 2 (RKNN_NPU_CORE_2 = 0x4).
# This is a runtime setting, not a conversion setting, but documented here for reference.
NPU_CORE_MASK = 0x4  # runtime: RKNN_DRIVING_CORE_MASK

# --- Helpers ----------------------------------------------------------------

def download_onnx_if_missing():
  if ONNX_PATH.exists():
    print(f"[ok] ONNX already present: {ONNX_PATH} ({ONNX_PATH.stat().st_size/1e6:.1f} MB)")
    return
  print(f"[download] fetching {ONNX_URL}")
  MODELS_DIR.mkdir(parents=True, exist_ok=True)
  import urllib.request
  urllib.request.urlretrieve(ONNX_URL, ONNX_PATH)
  print(f"[ok] downloaded: {ONNX_PATH} ({ONNX_PATH.stat().st_size/1e6:.1f} MB)")

def build_metadata(rknn: RKNN):
  """
  Capture the model's input/output shapes + build the output_slices dict the bukapilot
  parser expects. This is what driving_vision_metadata.pkl / driving_policy_metadata.pkl
  contain for the split model — we produce the equivalent for the single supercombo.

  output_slices maps named outputs (e.g. 'plan', 'action', 'leads') to slices of the flat
  output tensor. For 0.11 we need at minimum:
    - 'plan' (for the legacy derivation path, if action head is absent)
    - 'action' (if the model has a direct action head — get_action_from_model will use it)
    - 'leads', 'lane_lines', 'road_edges', 'meta', 'pose', 'desire_state', etc.
  """
  # TODO: inspect rknn.get_output_attrs() or load the ONNX with onnx.toolkit to enumerate
  #       named outputs and their sizes. The exact slice boundaries depend on the model's
  #       output ordering. Kommu's metadata pkl for the split model is the reference.
  print("[TODO] build_metadata: inspect model outputs and construct output_slices dict")
  print("       Reference: selfdrive/modeld/models/driving_vision_metadata.pkl (the split model's meta)")
  print("       Run: python3 -c \"import pickle; print(pickle.load(open('selfdrive/modeld/models/driving_vision_metadata.pkl','rb')))\"")
  # Placeholder — replace with real shape extraction:
  metadata = {
    "input_shapes": {},     # TODO: {name: shape} from the ONNX input nodes
    "output_shapes": {"outputs": []},  # TODO: flat output shape
    "output_slices": {},    # TODO: {name: slice} mapping
  }
  return metadata

# --- Main -------------------------------------------------------------------

def main():
  download_onnx_if_missing()

  print(f"\n[convert] {ONNX_PATH.name} -> {RKNN_PATH.name} (target={TARGET_PLATFORM}, fp16={QUANTIZE_FP16})")

  rknn = RKNN(verbose=True)

  # --- config ---
  # mean/std: ONNX models usually bake preprocessing in, so pass empty lists unless the
  # model's input node expects raw pixels. Inspect the ONNX input dtype/name to confirm.
  rknn.config(
    mean_values=[[]],       # TODO: verify against ONNX input node
    std_values=[[]],        # TODO: verify against ONNX input node
    target_platform=TARGET_PLATFORM,
    # Keep FP16 precision; do NOT quantize to INT8 (precision loss).
    quantized_dtype='w8a16' if QUANTIZE_FP16 else 'w8a8',
    quantized_method='channel',
    optimization_level=3,
  )

  # --- load ONNX ---
  ret = rknn.load_onnx(model=str(ONNX_PATH), outputs=None)  # outputs=None = take all
  if ret != 0:
    print(f"[FAIL] load_onnx returned {ret}. Likely an unsupported operator.", file=sys.stderr)
    print("       Check the verbose log above for which op failed.", file=sys.stderr)
    sys.exit(1)

  # --- build ---
  # do_quantization=False because we want FP16 weights (set above), not INT8 calibration.
  # If you have calibration data and want INT8, set do_quantization=True + provide a dataset.
  ret = rknn.build(do_quantization=False)
  if ret != 0:
    print(f"[FAIL] build returned {ret}.", file=sys.stderr)
    sys.exit(1)

  # --- export ---
  ret = rknn.export_rknn(str(RKNN_PATH))
  if ret != 0:
    print(f"[FAIL] export_rknn returned {ret}.", file=sys.stderr)
    sys.exit(1)

  print(f"\n[ok] RKNN exported: {RKNN_PATH} ({RKNN_PATH.stat().st_size/1e6:.1f} MB)")

  # --- metadata ---
  metadata = build_metadata(rknn)
  with open(META_PATH, "wb") as f:
    pickle.dump(metadata, f)
  print(f"[ok] metadata written: {META_PATH}")

  rknn.release()
  print("\n[DONE] Next steps:")
  print(f"  1. Copy {RKNN_PATH.name} + {META_PATH.name} to the KA2 at selfdrive/modeld/models/")
  print( "  2. Adapt ModelStateRKNN (modeld.py:386) to load the single model")
  print( "  3. Test on device: does modeld boot? does it hit 20 Hz? does the X70 drive?")

if __name__ == "__main__":
  main()
