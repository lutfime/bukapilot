#!/usr/bin/env python3
"""
INT8 quantized conversion of driving_supercombo.onnx → RKNN for Kommu KA2 (RK3588).

This is the INT8 variant of convert_011_model_to_rknn.py. It uses:
  - quantized_dtype='w8a8' (INT8 — ~2x faster than FP16 on RK3588 NPU)
  - do_quantization=True with calibration dataset
  - quantized_algorithm='mmse' (better scale selection than default min/max)

Expected result: ~66ms inference (vs 132ms FP16) → ~15Hz.
Accuracy risk: INT8 may degrade driving quality. Only a test drive can confirm.

MUST RUN IN DOCKER (RKNN-Toolkit2 is Ubuntu/aarch64). See MODEL-CONVERSION-GUIDE.md §3.
"""
import os, sys, pickle, base64
from pathlib import Path

import onnx
import numpy as np

try:
  from rknn.api import RKNN
except ImportError:
  print("ERROR: rknn-toolkit2 not installed. Run via Docker. See MODEL-CONVERSION-GUIDE.md §3.", file=sys.stderr)
  sys.exit(1)

REPO_ROOT = Path(os.environ.get("REPO_ROOT_OVERRIDE", "")).resolve() if os.environ.get("REPO_ROOT_OVERRIDE") else Path(__file__).resolve().parents[2]
MODELS_DIR = REPO_ROOT / "selfdrive" / "modeld" / "models"
CALIB_DIR = REPO_ROOT / "tools-local" / "calibration_frames"

ONNX_PATH = MODELS_DIR / "driving_supercombo.onnx"
RKNN_PATH = MODELS_DIR / "driving_supercombo_int8.rknn"
META_PATH = MODELS_DIR / "driving_supercombo_int8_metadata.pkl"
DATASET_TXT = CALIB_DIR / "dataset.txt"

TARGET_PLATFORM = "rk3588"

def check_prerequisites():
  """Verify ONNX exists and has been preprocessed (opset 19, float inputs)."""
  if not ONNX_PATH.exists():
    print(f"[FAIL] ONNX not found: {ONNX_PATH}", file=sys.stderr)
    print("       Run convert_011_model_to_rknn.py first (it downloads + preprocesses the ONNX).", file=sys.stderr)
    sys.exit(1)

  m = onnx.load(str(ONNX_PATH))
  opset = max(o.version for o in m.opset_import)
  if opset > 19:
    print(f"[WARN] opset is {opset} (>19). Run convert_011_model_to_rknn.py first to downconvert.", file=sys.stderr)

  # Check img input dtype (should be FLOAT after cast_uint8 preprocessing)
  for inp in m.graph.input:
    if inp.name in ('img', 'big_img') and inp.type.tensor_type.elem_type != 1:
      print(f"[WARN] {inp.name} is not FLOAT (type={inp.type.tensor_type.elem_type}).", file=sys.stderr)
      print("       Run convert_011_model_to_rknn.py first (it casts UINT8→FLOAT).", file=sys.stderr)
    break

  if not DATASET_TXT.exists():
    print(f"[FAIL] dataset.txt not found: {DATASET_TXT}", file=sys.stderr)
    print("       Run tools-local/calib_dataset.py first to generate calibration data.", file=sys.stderr)
    sys.exit(1)

  n_samples = sum(1 for _ in open(DATASET_TXT))
  print(f"[ok] ONNX: {ONNX_PATH.name} ({ONNX_PATH.stat().st_size/1e6:.1f} MB, opset {opset})")
  print(f"[ok] Dataset: {n_samples} calibration samples in {DATASET_TXT.name}")

def extract_metadata_from_onnx():
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
  def shape_of(ts):
    return tuple(d.dim_value if d.HasField('dim_value') else d.dim_param for d in ts.type.tensor_type.shape.dim)
  input_shapes = {inp.name: shape_of(inp) for inp in m.graph.input}
  output_shapes = {out.name: shape_of(out) for out in m.graph.output}
  return {
    'model_checkpoint': model_checkpoint,
    'output_slices': output_slices,
    'input_shapes': input_shapes,
    'output_shapes': output_shapes,
    'quantization': f'w8a8 (INT8) with {quant_algo} calibration, 217 samples',
  }

def main():
  check_prerequisites()
  metadata = extract_metadata_from_onnx()

  print(f"\n[convert] INT8 quantization: {ONNX_PATH.name} → {RKNN_PATH.name}")
  print(f"          dtype=w8a8, algorithm=mmse, {sum(1 for _ in open(DATASET_TXT))} calibration samples")

  rknn = RKNN(verbose=True)

  # --- config ---
  # INT8 quantization. Algorithm choice:
  #   'normal'  — default min/max, low memory, fast, slightly less accurate
  #   'mmse'    — MSE-minimizing, best accuracy but VERY memory-hungry (OOM'd on 8GB Colima)
  # Using 'normal' first to get a working model. Can retry MMSE on a bigger machine later.
  quant_algo = os.environ.get("QUANT_ALGORITHM", "normal")
  print(f"[config] quantized_algorithm={quant_algo}")
  rknn.config(
    mean_values=[[]],
    std_values=[[]],
    target_platform=TARGET_PLATFORM,
    quantized_dtype='w8a8',          # INT8 weights + activations
    quantized_method='channel',      # per-channel scales (better accuracy)
    quantized_algorithm=quant_algo,
    optimization_level=3,
  )

  # --- load ONNX ---
  ret = rknn.load_onnx(model=str(ONNX_PATH), outputs=None)
  if ret != 0:
    print(f"[FAIL] load_onnx returned {ret}.", file=sys.stderr)
    sys.exit(1)

  # --- build with quantization ---
  # This runs each calibration sample through the model to measure activation ranges.
  # Takes longer than FP16 build (several minutes for 200 samples).
  ret = rknn.build(
    do_quantization=True,
    dataset=str(DATASET_TXT),
  )
  if ret != 0:
    print(f"[FAIL] build returned {ret}.", file=sys.stderr)
    print("       Common causes:", file=sys.stderr)
    print("       - .npy shapes don't match model inputs", file=sys.stderr)
    print("       - dataset.txt has wrong number of paths per line", file=sys.stderr)
    print("       - unsupported op during quantization", file=sys.stderr)
    sys.exit(1)

  # --- export ---
  ret = rknn.export_rknn(str(RKNN_PATH))
  if ret != 0:
    print(f"[FAIL] export_rknn returned {ret}.", file=sys.stderr)
    sys.exit(1)

  print(f"\n[ok] INT8 RKNN exported: {RKNN_PATH} ({RKNN_PATH.stat().st_size/1e6:.1f} MB)")

  # Compare sizes
  fp16_path = MODELS_DIR / "driving_supercombo.rknn"
  if fp16_path.exists():
    print(f"     FP16 was: {fp16_path.stat().st_size/1e6:.1f} MB")
    print(f"     INT8 is:  {RKNN_PATH.stat().st_size/1e6:.1f} MB ({RKNN_PATH.stat().st_size/fp16_path.stat().st_size*100:.0f}%)")

  # --- metadata ---
  with open(META_PATH, "wb") as f:
    pickle.dump(metadata, f)
  print(f"[ok] metadata written: {META_PATH}")

  rknn.release()
  print("\n[DONE] INT8 conversion succeeded. Next steps:")
  print(f"  1. Copy {RKNN_PATH.name} + {META_PATH.name} to the KA2")
  print( "  2. Benchmark: expect ~66ms (vs 132ms FP16) → ~15Hz")
  print( "  3. Test drive to verify accuracy (INT8 may degrade driving quality)")
  print( "  4. If accuracy is bad, try auto_hybrid or revert to FP16")

if __name__ == "__main__":
  main()
