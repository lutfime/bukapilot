#!/usr/bin/env python3
"""
Convert the standard openpilot 0.11 driving model (driving_supercombo.onnx) to RKNN
for the Kommu KA2 (Rockchip RK3588 NPU).

MUST RUN ON UBUNTU (RKNN-Toolkit2 is Ubuntu-only). Run via Docker:
    docker run --rm -v "$PWD":/work -w /work ubuntu:22.04 bash -c "
      apt-get update && apt-get install -y python3.10 python3-pip wget \\
      && pip3 install rknn-toolkit2 onnx numpy \\
      && python3 tools-local/convert_011_model_to_rknn.py"

Or on a Linux box / VM with RKNN-Toolkit2 installed:
    python3 tools-local/convert_011_model_to_rknn.py

Output: selfdrive/modeld/models/driving_supercombo.rknn (+ metadata pkl)

Findings from inspecting the model (2026-08-04):
- 481 nodes, opset 20, IR 10 — standard transformer ops (MatMul/Softmax/LayerNorm)
- Only one mildly risky op: GatherND (x1) — usually supported on RK3588
- NO FlashAttention / Attention / GridSample (those would block conversion)
- The output_slices are EMBEDDED in the ONNX metadata as base64 pickle — extracted below
- The model HAS a direct action head (slice 2062-2066)
"""
import os
import sys
import pickle
import base64
from pathlib import Path

import onnx
import numpy as np

try:
  from rknn.api import RKNN
except ImportError:
  print("ERROR: rknn-toolkit2 not installed. Ubuntu-only. See docstring.", file=sys.stderr)
  print("  pip install rknn-toolkit2", file=sys.stderr)
  sys.exit(1)

# --- Configuration ----------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[2]
MODELS_DIR = REPO_ROOT / "selfdrive" / "modeld" / "models"

ONNX_URL = "https://github.com/commaai/openpilot/raw/master/openpilot/selfdrive/modeld/models/driving_supercombo.onnx"
ONNX_PATH = MODELS_DIR / "driving_supercombo.onnx"
RKNN_PATH = MODELS_DIR / "driving_supercombo.rknn"
META_PATH = MODELS_DIR / "driving_supercombo_metadata.pkl"

TARGET_PLATFORM = "rk3588"

# FP16 quantization — Kommu's approach (matches their runtime casting inputs to float16).
# INT8 loses too much precision (RKPilot/sunnypilot-pc rejected RKNN for this reason).
QUANTIZE_FP16 = True

# --- Helpers ----------------------------------------------------------------

def download_onnx_if_missing():
  if ONNX_PATH.exists() and ONNX_PATH.stat().st_size > 10_000_000:
    print(f"[ok] ONNX already present: {ONNX_PATH} ({ONNX_PATH.stat().st_size/1e6:.1f} MB)")
    return
  print(f"[download] fetching {ONNX_URL} (via curl -L, ~92 MB)")
  MODELS_DIR.mkdir(parents=True, exist_ok=True)
  import subprocess
  ret = subprocess.call(["curl", "-sL", ONNX_URL, "-o", str(ONNX_PATH)])
  if ret != 0 or ONNX_PATH.stat().st_size < 10_000_000:
    print(f"[FAIL] download failed (size={ONNX_PATH.stat().st_size if ONNX_PATH.exists() else 0})", file=sys.stderr)
    sys.exit(1)
  print(f"[ok] downloaded: {ONNX_PATH} ({ONNX_PATH.stat().st_size/1e6:.1f} MB)")

def extract_metadata_from_onnx():
  """
  The output_slices ship as a base64-encoded pickle in the ONNX metadata_props.
  This matches upstream's make_metadata_dict() in get_model_metadata.py — we read them
  back directly instead of recomputing. Also grabs input_shapes + output_shapes from
  the graph, and the model_checkpoint.
  """
  print(f"\n[metadata] extracting from {ONNX_PATH.name}")
  m = onnx.load(str(ONNX_PATH))

  output_slices_b64 = None
  model_checkpoint = None
  for prop in m.metadata_props:
    if prop.key == 'output_slices':
      output_slices_b64 = prop.value
    elif prop.key == 'model_checkpoint':
      model_checkpoint = prop.value

  if output_slices_b64 is None:
    print("[FAIL] output_slices not found in ONNX metadata", file=sys.stderr)
    sys.exit(1)

  output_slices = pickle.loads(base64.b64decode(output_slices_b64))
  print(f"[ok] model_checkpoint: {model_checkpoint}")
  print(f"[ok] output_slices ({len(output_slices)} entries):")
  for name, sl in output_slices.items():
    size = (sl.stop - sl.start) if hasattr(sl, 'stop') and sl.stop is not None else '?'
    print(f"       {name:30s} {sl}  (size: {size})")

  # input_shapes + output_shapes from the graph
  def shape_of(ts):
    dims = []
    for d in ts.type.tensor_type.shape.dim:
      dims.append(d.dim_value if d.HasField('dim_value') else d.dim_param)
    return tuple(dims)

  input_shapes = {inp.name: shape_of(inp) for inp in m.graph.input}
  output_shapes = {out.name: shape_of(out) for out in m.graph.output}

  metadata = {
    'model_checkpoint': model_checkpoint,
    'output_slices': output_slices,
    'input_shapes': input_shapes,
    'output_shapes': output_shapes,
  }
  return metadata

# --- Main -------------------------------------------------------------------

def main():
  download_onnx_if_missing()
  metadata = extract_metadata_from_onnx()

  print(f"\n[convert] {ONNX_PATH.name} -> {RKNN_PATH.name} (target={TARGET_PLATFORM}, fp16={QUANTIZE_FP16})")

  rknn = RKNN(verbose=True)

  # --- config ---
  # mean/std empty: preprocessing is baked into the ONNX (inputs are uint8 with internal normalization)
  rknn.config(
    mean_values=[[]],
    std_values=[[]],
    target_platform=TARGET_PLATFORM,
    quantized_dtype='w8a16' if QUANTIZE_FP16 else 'w8a8',
    quantized_method='channel',
    optimization_level=3,
  )

  # --- load ONNX ---
  ret = rknn.load_onnx(model=str(ONNX_PATH), outputs=None)
  if ret != 0:
    print(f"[FAIL] load_onnx returned {ret}. Likely an unsupported operator.", file=sys.stderr)
    print("       Verbose log above should name the failing op.", file=sys.stderr)
    print("       Expected OK: only GatherND is mildly risky in this model.", file=sys.stderr)
    sys.exit(1)

  # --- build ---
  # do_quantization=False: we want FP16 weights (set above), not INT8 calibration.
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
  with open(META_PATH, "wb") as f:
    pickle.dump(metadata, f)
  print(f"[ok] metadata written: {META_PATH}")

  rknn.release()
  print("\n[DONE] Conversion succeeded. Next steps:")
  print(f"  1. Copy {RKNN_PATH.name} + {META_PATH.name} to the KA2 at selfdrive/modeld/models/")
  print( "  2. Adapt ModelStateRKNN (modeld.py:386) to load the single model:")
  print( "     - Load driving_supercombo.rknn instead of vision+policy split")
  print( "     - Feed all 6 inputs (img, big_img, desire_pulse, traffic_convention, action_t, features_buffer)")
  print( "     - Note: features_buffer is [1,24,512] on 0.11 (was [1,25,512] on 0.10)")
  print( "     - Run once, slice the flat 2580 output per the metadata output_slices")
  print( "  3. Force Python runner for first test: RKNN_USE_PYTHON=1")
  print( "  4. Test on device: does modeld boot? hit 20 Hz? X70 drives sanely?")

if __name__ == "__main__":
  main()
