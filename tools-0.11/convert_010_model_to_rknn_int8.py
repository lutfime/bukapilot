#!/usr/bin/env python3
"""
INT8 quantized conversion of 0.10.3 split models (vision + policy) → RKNN.

This converts the ORIGINAL comma 0.10.3 models to INT8 to compare against
Kommu's FP16 production models. The question: does INT8 help the 0.10 models
that already run at 32.8ms?

Model inputs:
  driving_vision.onnx:  img [1,12,128,256] UINT8, big_img [1,12,128,256] UINT8
  driving_policy.onnx:  desire_pulse [1,25,8], traffic_convention [1,2], features_buffer [1,25,512]

MUST RUN IN DOCKER. See MODEL-CONVERSION-GUIDE.md §3.
"""
import os, sys, pickle, base64, subprocess
from pathlib import Path

import onnx
import numpy as np

try:
  from rknn.api import RKNN
except ImportError:
  print("ERROR: rknn-toolkit2 not installed. Run via Docker.", file=sys.stderr)
  sys.exit(1)

REPO_ROOT = Path(os.environ.get("REPO_ROOT_OVERRIDE", "")).resolve() if os.environ.get("REPO_ROOT_OVERRIDE") else Path(__file__).resolve().parents[2]
MODELS_DIR = REPO_ROOT / "selfdrive" / "modeld" / "models"
CALIB_DIR = REPO_ROOT / "tools-local" / "calibration_frames"

TARGET_PLATFORM = "rk3588"

def cast_uint8_to_float(onnx_path):
  """Cast UINT8 image inputs → FLOAT (same as cast_uint8_inputs_to_float.py)."""
  m = onnx.load(str(onnx_path))
  changed = False
  for inp in m.graph.input:
    if inp.type.tensor_type.elem_type == 2:  # UINT8
      inp.type.tensor_type.elem_type = 1  # FLOAT
      changed = True
  if changed:
    onnx.save(m, str(onnx_path))
    print(f"  [cast] UINT8 inputs → FLOAT in {onnx_path.name}")

def extract_metadata(onnx_path):
  m = onnx.load(str(onnx_path))
  output_slices_b64 = None
  model_checkpoint = None
  for prop in m.metadata_props:
    if prop.key == 'output_slices': output_slices_b64 = prop.value
    elif prop.key == 'model_checkpoint': model_checkpoint = prop.value
  output_slices = pickle.loads(base64.b64decode(output_slices_b64)) if output_slices_b64 else {}
  def shape_of(ts):
    return tuple(d.dim_value if d.HasField('dim_value') else d.dim_param for d in ts.type.tensor_type.shape.dim)
  return {
    'model_checkpoint': model_checkpoint,
    'output_slices': output_slices,
    'input_shapes': {inp.name: shape_of(inp) for inp in m.graph.input},
    'output_shapes': {out.name: shape_of(out) for out in m.graph.output},
    'quantization': 'w8a8 (INT8) normal calibration',
  }

def build_calibration_dataset_vision():
  """Build dataset.txt for vision model (img + big_img inputs)."""
  import glob
  from PIL import Image
  OUTPUT_DIR = CALIB_DIR / "calib_vision_npy"
  DATASET = CALIB_DIR / "dataset_vision.txt"
  OUTPUT_DIR.mkdir(exist_ok=True)
  for f in glob.glob(str(OUTPUT_DIR / "*")): os.remove(f)

  EXTRACTED = REPO_ROOT / "tools-local" / "calibration_frames" / "extracted"
  segments = {}
  for d in sorted(glob.glob(str(EXTRACTED / "*"))):
    seg = os.path.basename(d)
    base = seg.rsplit("_", 1)[0]
    cam = seg.rsplit("_", 1)[1]
    segments.setdefault(base, {})
    for f in sorted(glob.glob(f"{d}/frame_*.jpg")):
      fname = os.path.basename(f)
      segments[base].setdefault(fname, {})[cam] = f

  pairs = []
  for base, frames in sorted(segments.items()):
    for fname, cams in sorted(frames.items()):
      if 'fcamera' in cams and 'ecamera' in cams:
        pairs.append((cams['fcamera'], cams['ecamera']))

  if len(pairs) > 200:
    step = len(pairs) / 200
    pairs = [pairs[int(i*step)] for i in range(200)]

  def transform(img_path):
    im = Image.open(img_path).convert("L").resize((256, 128), Image.BILINEAR)
    arr = np.asarray(im, dtype=np.float32)
    single = arr.reshape(1, 1, 128, 256)
    return np.tile(single, (1, 12, 1, 1))

  lines = []
  for i, (fcam, ecam) in enumerate(pairs):
    img_p = OUTPUT_DIR / f"s{i:04d}_img.npy"
    big_p = OUTPUT_DIR / f"s{i:04d}_big.npy"
    np.save(img_p, transform(fcam))
    np.save(big_p, transform(ecam))
    lines.append(f"{OUTPUT_DIR.name}/s{i:04d}_img.npy {OUTPUT_DIR.name}/s{i:04d}_big.npy")

  DATASET.write_text("\n".join(lines) + "\n")
  print(f"  [dataset] {len(pairs)} vision calibration samples → {DATASET.name}")
  # Debug: verify first sample loads
  print(f"  [verify] {OUTPUT_DIR}/s0000_img.npy exists: {(OUTPUT_DIR / 's0000_img.npy').exists()}")
  test = np.load(OUTPUT_DIR / "s0000_img.npy")
  print(f"  [verify] shape={test.shape} dtype={test.dtype}")
  return DATASET

def build_calibration_dataset_policy():
  """Build dataset.txt for policy model (desire_pulse, traffic_convention, features_buffer)."""
  OUTPUT_DIR = CALIB_DIR / "calib_policy_npy"
  DATASET = CALIB_DIR / "dataset_policy.txt"
  OUTPUT_DIR.mkdir(exist_ok=True)
  for f in glob.glob(str(OUTPUT_DIR / "*")): os.remove(f)

  # Policy takes non-vision inputs. Use zeros/default values for calibration.
  # 200 samples with slight randomization for features_buffer
  lines = []
  n = 100  # fewer needed for non-vision inputs
  for i in range(n):
    desire = np.zeros((1, 25, 8), dtype=np.float32)
    tc = np.array([[1.0, 0.0]], dtype=np.float32)
    fb = np.random.randn(1, 25, 512).astype(np.float32) * 0.1  # small random for calibration
    for name, arr in [('desire', desire), ('tc', tc), ('fb', fb)]:
      np.save(OUTPUT_DIR / f"s{i:04d}_{name}.npy", arr)
    lines.append(f"calib_policy_npy/s{i:04d}_desire.npy calib_policy_npy/s{i:04d}_tc.npy calib_policy_npy/s{i:04d}_fb.npy")

  DATASET.write_text("\n".join(lines) + "\n")
  print(f"  [dataset] {n} policy calibration samples")
  return DATASET

def convert_model(onnx_path, rknn_path, meta_path, dataset_path):
  print(f"\n{'='*60}")
  print(f"Converting: {onnx_path.name}")
  print(f"  → {rknn_path.name}")
  print(f"  dataset: {dataset_path.name}")
  print(f"{'='*60}")

  metadata = extract_metadata(onnx_path)

  rknn = RKNN(verbose=False)
  rknn.config(
    mean_values=[[]], std_values=[[]],
    target_platform=TARGET_PLATFORM,
    quantized_dtype='w8a8',
    quantized_method='channel',
    quantized_algorithm='normal',
    optimization_level=2,  # level 3 triggers zero-size array bug on 0.10 vision; level 2 is safer
  )

  ret = rknn.load_onnx(model=str(onnx_path), outputs=None)
  if ret != 0:
    print(f"[FAIL] load_onnx: {ret}", file=sys.stderr); sys.exit(1)

  ret = rknn.build(do_quantization=True, dataset=str(dataset_path))
  if ret != 0:
    print(f"[FAIL] build: {ret}", file=sys.stderr); sys.exit(1)

  ret = rknn.export_rknn(str(rknn_path))
  if ret != 0:
    print(f"[FAIL] export: {ret}", file=sys.stderr); sys.exit(1)

  with open(meta_path, "wb") as f:
    pickle.dump(metadata, f)

  size_mb = rknn_path.stat().st_size / 1e6
  print(f"[ok] {rknn_path.name}: {size_mb:.1f} MB")
  rknn.release()

import glob

def main():
  # --- Vision model ---
  vision_onnx = MODELS_DIR / "driving_vision.onnx"
  vision_rknn = MODELS_DIR / "driving_vision_int8.rknn"
  vision_meta = MODELS_DIR / "driving_vision_int8_metadata.pkl"

  if not vision_onnx.exists():
    print(f"[FAIL] {vision_onnx} not found", file=sys.stderr); sys.exit(1)

  print("[step 1] Cast UINT8 → FLOAT (vision)")
  cast_uint8_to_float(vision_onnx)

  print("[step 2] Build vision calibration dataset")
  vision_dataset = build_calibration_dataset_vision()

  convert_model(vision_onnx, vision_rknn, vision_meta, vision_dataset)

  # --- Policy model ---
  policy_onnx = MODELS_DIR / "driving_policy.onnx"
  policy_rknn = MODELS_DIR / "driving_policy_int8.rknn"
  policy_meta = MODELS_DIR / "driving_policy_int8_metadata.pkl"

  if not policy_onnx.exists():
    print(f"[FAIL] {policy_onnx} not found", file=sys.stderr); sys.exit(1)

  print("\n[step 3] Build policy calibration dataset")
  policy_dataset = build_calibration_dataset_policy()

  convert_model(policy_onnx, policy_rknn, policy_meta, policy_dataset)

  print(f"\n{'='*60}")
  print("DONE! Both INT8 models created:")
  print(f"  {vision_rknn.name}: {vision_rknn.stat().st_size/1e6:.1f} MB")
  print(f"  {policy_rknn.name}: {policy_rknn.stat().st_size/1e6:.1f} MB")
  print(f"\nNext: benchmark on device with bench_rknn.c (see guide §11)")
  print(f"  /tmp/bench_rknn <vision_int8.rknn>")
  print(f"  /tmp/bench_rknn <policy_int8.rknn>")

if __name__ == "__main__":
  main()
