#!/usr/bin/env python3
"""
Convert the OP Model 10 V3 driving model (3-file split: vision + on_policy + off_policy)
to RKNN for the Kommu KA2 (Rockchip RK3588 NPU).

This is comma's latest split architecture (post-0.11), where perception outputs
(lane_lines, road_edges, lead) moved from the vision model into a dedicated
off_policy head. The vision model is leaner (632 outputs vs 1576 in 0.10.3).

MUST RUN ON UBUNTU (RKNN-Toolkit2 is Ubuntu-only). Run via Docker — see
run_opm10v3_conversion.sh.

The ONNX source files come from commaai/openpilot at commit 7d4c295c27bc...,
resolved via git lfs smudge (the LFS batch API returns 404 but smudge works).
See MODEL-CONVERSION-GUIDE.md §2 for the conversion pipeline details.

Output: selfdrive/modeld/models/driving_{vision,on_policy,off_policy}_opm10v3.rknn
        + matching _metadata.pkl files
"""
import os
import sys
import pickle
import base64
import subprocess
from pathlib import Path

import onnx
import numpy as np

try:
  from rknn.api import RKNN
except ImportError:
  print("ERROR: rknn-toolkit2 not installed. Ubuntu-only. See run_opm10v3_conversion.sh.", file=sys.stderr)
  sys.exit(1)

# --- Configuration ----------------------------------------------------------

REPO_ROOT = Path(os.environ.get("REPO_ROOT_OVERRIDE", "")).resolve() if os.environ.get("REPO_ROOT_OVERRIDE") else Path(__file__).resolve().parents[2]
MODELS_DIR = REPO_ROOT / "selfdrive" / "modeld" / "models"
TARGET_PLATFORM = "rk3588"
QUANTIZE_FP16 = True  # matches bukapilot runtime (inputs cast to float16)

# The 3 sub-models. Files are named with _opm10v3 suffix to avoid clobbering
# the current production models (driving_vision.rknn, driving_policy.rknn).
SUFFIX = "opm10v3"
MODELS = [
  {
    "onnx": "driving_vision.onnx",
    "rknn": f"driving_vision_{SUFFIX}.rknn",
    "meta": f"driving_vision_{SUFFIX}_metadata.pkl",
    "cast_uint8": True,   # vision has img/big_img as UINT8
    "gelu_count_hint": 38,
  },
  {
    "onnx": "driving_on_policy.onnx",
    "rknn": f"driving_on_policy_{SUFFIX}.rknn",
    "meta": f"driving_on_policy_{SUFFIX}_metadata.pkl",
    "cast_uint8": False,  # policy heads have no image inputs
    "gelu_count_hint": 9,
  },
  {
    "onnx": "driving_off_policy.onnx",
    "rknn": f"driving_off_policy_{SUFFIX}.rknn",
    "meta": f"driving_off_policy_{SUFFIX}_metadata.pkl",
    "cast_uint8": False,
    "gelu_count_hint": 21,
  },
]

# --- Helpers ----------------------------------------------------------------

def cast_uint8_inputs(onnx_path: Path):
  """RKNN-Toolkit2 w16a16i build rejects UINT8 inputs ("Not Support Dtype: 2").
  Cast img/big_img from UINT8 to FLOAT (matches bukapilot runtime which casts to float16)."""
  print(f"  [cast] UINT8 -> FLOAT for {onnx_path.name}")
  ret = subprocess.call([sys.executable, str(Path(__file__).parent / "cast_uint8_inputs_to_float.py"), str(onnx_path)])
  if ret != 0:
    print(f"  [FAIL] UINT8 cast failed (exit {ret})", file=sys.stderr)
    sys.exit(1)

def downconvert_opset_if_needed(onnx_path: Path, max_opset: int = 19):
  """RKNN-Toolkit2 v2.3.2 supports ONNX opset <= 19; OPM10V3 models are opset 20.
  The opset-20-only op is native Gelu. rewrite_gelu_for_opset19.py replaces
  each Gelu with its erf-subgraph, then downconverts."""
  m = onnx.load(str(onnx_path))
  current = max((o.version for o in m.opset_import), default=0)
  if current <= max_opset:
    print(f"  [ok] opset {current} <= {max_opset}, no downconvert needed")
    return
  print(f"  [downconvert] opset {current} -> {max_opset}")
  ret = subprocess.call([sys.executable, str(Path(__file__).parent / "rewrite_gelu_for_opset19.py"), str(onnx_path)])
  if ret != 0:
    print(f"  [FAIL] Gelu rewrite failed (exit {ret})", file=sys.stderr)
    sys.exit(1)
  print(f"  [ok] saved as opset {max_opset}")

def extract_metadata(onnx_path: Path):
  """Extract output_slices (base64 pickle in metadata_props), input_shapes,
  output_shapes, and model_checkpoint from the ONNX graph."""
  m = onnx.load(str(onnx_path))

  output_slices_b64 = None
  model_checkpoint = None
  for prop in m.metadata_props:
    if prop.key == 'output_slices':
      output_slices_b64 = prop.value
    elif prop.key == 'model_checkpoint':
      model_checkpoint = prop.value

  if output_slices_b64 is None:
    print(f"  [FAIL] output_slices not found in ONNX metadata", file=sys.stderr)
    sys.exit(1)

  output_slices = pickle.loads(base64.b64decode(output_slices_b64))

  def shape_of(ts):
    dims = []
    for d in ts.type.tensor_type.shape.dim:
      dims.append(d.dim_value if d.HasField('dim_value') else d.dim_param)
    return tuple(dims)

  input_shapes = {inp.name: shape_of(inp) for inp in m.graph.input}
  output_shapes = {out.name: shape_of(out) for out in m.graph.output}

  return {
    'model_checkpoint': model_checkpoint,
    'output_slices': output_slices,
    'input_shapes': input_shapes,
    'output_shapes': output_shapes,
  }

# --- Main -------------------------------------------------------------------

def convert_one(cfg: dict):
  """Convert a single ONNX model to RKNN + extract metadata."""
  onnx_path = MODELS_DIR / cfg["onnx"]
  rknn_path = MODELS_DIR / cfg["rknn"]
  meta_path = MODELS_DIR / cfg["meta"]

  print(f"\n{'='*70}")
  print(f"  Converting: {cfg['onnx']} -> {cfg['rknn']}")
  print(f"{'='*70}")

  if not onnx_path.exists():
    print(f"  [FAIL] {onnx_path} not found", file=sys.stderr)
    sys.exit(1)

  # Check if this ONNX was already processed (opset 19 = already downconverted)
  m = onnx.load(str(onnx_path))
  current_opset = max((o.version for o in m.opset_import), default=0)
  print(f"  [info] {onnx_path.name}: {onnx_path.stat().st_size/1e6:.1f} MB, opset {current_opset}")

  # Step 1: extract metadata BEFORE modifying the ONNX (output_slices are in the original)
  print(f"  [metadata] extracting from {onnx_path.name}")
  metadata = extract_metadata(onnx_path)
  print(f"  [ok] checkpoint: {metadata['model_checkpoint']}")
  print(f"  [ok] output_slices ({len(metadata['output_slices'])} entries):")
  for name, sl in metadata['output_slices'].items():
    size = (sl.stop - sl.start) if hasattr(sl, 'stop') and sl.stop is not None else '?'
    print(f"         {name:30s} {sl}  (size: {size})")

  # Step 2: cast UINT8 inputs (vision only)
  if cfg["cast_uint8"]:
    cast_uint8_inputs(onnx_path)

  # Step 3: rewrite Gelu + downconvert opset 20 -> 19
  downconvert_opset_if_needed(onnx_path, max_opset=19)

  # Step 4: RKNN convert
  print(f"  [convert] {onnx_path.name} -> {rknn_path.name} (target={TARGET_PLATFORM}, fp16={QUANTIZE_FP16})")

  rknn = RKNN(verbose=True)

  rknn.config(
    mean_values=[[]],
    std_values=[[]],
    target_platform=TARGET_PLATFORM,
    quantized_dtype='w16a16i' if QUANTIZE_FP16 else 'w8a8',
    quantized_method='channel',
    optimization_level=3,
  )

  ret = rknn.load_onnx(model=str(onnx_path), outputs=None)
  if ret != 0:
    print(f"  [FAIL] load_onnx returned {ret}. Likely an unsupported operator.", file=sys.stderr)
    print(f"         Verbose log above should name the failing op.", file=sys.stderr)
    sys.exit(1)

  ret = rknn.build(do_quantization=False)
  if ret != 0:
    print(f"  [FAIL] build returned {ret}.", file=sys.stderr)
    sys.exit(1)

  ret = rknn.export_rknn(str(rknn_path))
  if ret != 0:
    print(f"  [FAIL] export_rknn returned {ret}.", file=sys.stderr)
    sys.exit(1)

  print(f"  [ok] RKNN exported: {rknn_path.name} ({rknn_path.stat().st_size/1e6:.1f} MB)")
  rknn.release()

  # Step 5: save metadata
  with open(meta_path, "wb") as f:
    pickle.dump(metadata, f)
  print(f"  [ok] metadata written: {meta_path.name}")

def main():
  print(f"OPM10V3 (3-file split) -> RKNN conversion")
  print(f"  repo root: {REPO_ROOT}")
  print(f"  models dir: {MODELS_DIR}")
  print(f"  target: {TARGET_PLATFORM}, fp16={QUANTIZE_FP16}")
  print(f"  suffix: _{SUFFIX} (production models are NOT touched)")

  # Verify all ONNX files are present before starting
  missing = []
  for cfg in MODELS:
    onnx_path = MODELS_DIR / cfg["onnx"]
    if not onnx_path.exists():
      missing.append(str(onnx_path))
  if missing:
    print(f"\n[FAIL] Missing ONNX files:", file=sys.stderr)
    for m in missing:
      print(f"  {m}", file=sys.stderr)
    print(f"\nCopy the OPM10V3 ONNX files to {MODELS_DIR}/ first.", file=sys.stderr)
    print(f"They are at /tmp/sp-models/opm10v3-onnx/ on this machine.", file=sys.stderr)
    sys.exit(1)

  for cfg in MODELS:
    convert_one(cfg)

  print(f"\n{'='*70}")
  print(f"[DONE] All 3 models converted. Output files:")
  print(f"{'='*70}")
  for cfg in MODELS:
    rknn_path = MODELS_DIR / cfg["rknn"]
    meta_path = MODELS_DIR / cfg["meta"]
    rknn_size = rknn_path.stat().st_size / 1e6 if rknn_path.exists() else 0
    meta_size = meta_path.stat().st_size if meta_path.exists() else 0
    print(f"  {rknn_path.name:45} {rknn_size:6.1f} MB")
    print(f"  {meta_path.name:45} {meta_size:>6} B")
  print(f"\nNext steps:")
  print(f"  1. Copy the 6 files to the KA2 at selfdrive/modeld/models/")
  print(f"  2. Build the 3-split runtime (new ModelState3SplitRKNN class)")
  print(f"  3. Test: RKNN_USE_PYTHON=1 modeld")

if __name__ == "__main__":
  main()
