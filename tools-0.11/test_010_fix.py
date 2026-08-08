#!/usr/bin/env python3
"""
Fix and convert 0.10 vision model to INT8.

Root cause of quantization failure: an initializer 'pad' with dims [1, 0] (zero elements).
RKNN's get_const_min_max() crashes on np.min([]).

Fix: remove the zero-size initializer and any nodes that reference it.
Also try auto_hybrid as a fallback.
"""
import os, sys, pickle, base64, glob
from pathlib import Path
import numpy as np
from PIL import Image

import onnx
from onnx import helper, TensorProto

try:
  from rknn.api import RKNN
except ImportError:
  print("ERROR: rknn-toolkit2 not installed. Run via Docker.", file=sys.stderr)
  sys.exit(1)

REPO_ROOT = Path(os.environ.get("REPO_ROOT_OVERRIDE", "")).resolve() if os.environ.get("REPO_ROOT_OVERRIDE") else Path(__file__).resolve().parents[2]
MODELS_DIR = REPO_ROOT / "selfdrive" / "modeld" / "models"
CALIB_DIR = REPO_ROOT / "tools-local" / "calibration_frames"
ORIG_ONNX = MODELS_DIR / "driving_vision.onnx"          # pristine original, never modify
WORK_ONNX = MODELS_DIR / "driving_vision_fixed.onnx"     # working copy we modify

def remove_zero_size_initializers(onnx_path):
  """Remove zero-size initializers and any nodes that reference them."""
  m = onnx.load(str(onnx_path))

  # Find zero-size initializers
  to_remove = set()
  for init in m.graph.initializer:
    dims = list(init.dims)
    if 0 in dims:
      print(f"  [fix] Found zero-size initializer: {init.name} dims={dims}")
      to_remove.add(init.name)

  if not to_remove:
    print("  [fix] No zero-size initializers found")
    return

  # Find nodes that use zero-size initializers as inputs
  nodes_to_remove = []
  for node in m.graph.node:
    if any(inp in to_remove for inp in node.input):
      print(f"  [fix] Node uses zero-size tensor: {node.op_type} {node.name} inputs={list(node.input)}")
      # Don't remove the node — just replace the zero-size input with a small valid tensor
      # Actually, let's replace the zero-size initializer with a [1] tensor containing 0

  # Strategy: replace zero-size initializers with same-rank valid tensors filled with 0
  # Keep the same number of dims, just replace 0-size dims with 1
  new_initializers = []
  for init in m.graph.initializer:
    dims = list(init.dims)
    if 0 in dims:
      # Replace 0 dims with 1, keep rank the same
      new_dims = [max(1, d) for d in dims]
      total_elems = 1
      for d in new_dims: total_elems *= d
      print(f"  [fix] Replacing {init.name} dims={dims} → {new_dims} ({total_elems} elems, value=0)")
      import struct
      new_init = TensorProto()
      new_init.name = init.name
      new_init.data_type = init.data_type
      new_init.dims.extend(new_dims)
      # Fill with zeros using raw_data
      if init.data_type == 1:  # FLOAT32
        new_init.raw_data = struct.pack(f'<{total_elems}f', *([0.0] * total_elems))
      elif init.data_type == 11:  # DOUBLE
        new_init.raw_data = struct.pack(f'<{total_elems}d', *([0.0] * total_elems))
      elif init.data_type == 7:  # INT64
        new_init.raw_data = struct.pack(f'<{total_elems}q', *([0] * total_elems))
      elif init.data_type == 6:  # INT32
        new_init.raw_data = struct.pack(f'<{total_elems}i', *([0] * total_elems))
      else:
        new_init.data_type = 1
        new_init.raw_data = struct.pack(f'<{total_elems}f', *([0.0] * total_elems))
      new_initializers.append(new_init)
    else:
      new_initializers.append(init)

  # Replace initializers
  del m.graph.initializer[:]
  m.graph.initializer.extend(new_initializers)

  onnx.save(m, str(onnx_path))
  print(f"  [fix] Saved fixed ONNX: {onnx_path}")

def cast_uint8_to_float(onnx_path):
  m = onnx.load(str(onnx_path))
  changed = False
  for inp in m.graph.input:
    if inp.type.tensor_type.elem_type == 2:
      inp.type.tensor_type.elem_type = 1
      changed = True
  if changed:
    onnx.save(m, str(onnx_path))
    print(f"  [cast] UINT8 → FLOAT")

def build_dataset():
  """Build calibration dataset for vision model (2 inputs: img + big_img)."""
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

  def transform(img_path):
    im = Image.open(img_path).convert("L").resize((256, 128), Image.BILINEAR)
    arr = np.asarray(im, dtype=np.float32)
    single = arr.reshape(1, 1, 128, 256)
    return np.tile(single, (1, 12, 1, 1))

  lines = []
  for i, (fcam, ecam) in enumerate(pairs):
    np.save(OUTPUT_DIR / f"s{i:04d}_img.npy", transform(fcam))
    np.save(OUTPUT_DIR / f"s{i:04d}_big.npy", transform(ecam))
    lines.append(f"{OUTPUT_DIR.name}/s{i:04d}_img.npy {OUTPUT_DIR.name}/s{i:04d}_big.npy")

  DATASET.write_text("\n".join(lines) + "\n")
  print(f"  [dataset] {len(pairs)} samples")
  return DATASET

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
    'quantization': 'w8a8 INT8 normal + zero-fix + auto_hybrid',
  }

def main():
  if not ORIG_ONNX.exists():
    print(f"[FAIL] {ORIG_ONNX} not found", file=sys.stderr); sys.exit(1)

  # Work on a copy — never modify the original
  import shutil
  shutil.copy2(ORIG_ONNX, WORK_ONNX)
  print(f"[init] Working on copy: {WORK_ONNX.name}")

  print("[step 1] Fix zero-size initializers")
  remove_zero_size_initializers(WORK_ONNX)

  print("[step 2] Cast UINT8 → FLOAT")
  cast_uint8_to_float(WORK_ONNX)

  print("[step 3] Build calibration dataset")
  dataset = build_dataset()

  print("[step 4] Convert with INT8 + auto_hybrid")
  rknn_path = MODELS_DIR / "driving_vision_int8.rknn"
  meta_path = MODELS_DIR / "driving_vision_int8_metadata.pkl"
  metadata = extract_metadata(WORK_ONNX)

  rknn = RKNN(verbose=False)
  rknn.config(
    mean_values=[[]], std_values=[[]],
    target_platform="rk3588",
    quantized_dtype='w8a8',
    quantized_method='channel',
    quantized_algorithm='normal',
    optimization_level=3,
  )

  ret = rknn.load_onnx(model=str(WORK_ONNX), outputs=None)
  print(f"  load_onnx: {ret}")
  if ret != 0: sys.exit(1)

  # Try WITHOUT auto_hybrid first (simpler, less memory, avoids segfault risk)
  ret = rknn.build(do_quantization=True, dataset=str(dataset))
  print(f"  build (no hybrid): {ret}")

  if ret != 0:
    print("  [retry] build failed, trying with auto_hybrid...")
    rknn.release()
    rknn = RKNN(verbose=False)
    rknn.config(
      mean_values=[[]], std_values=[[]],
      target_platform="rk3588",
      quantized_dtype='w8a8',
      quantized_method='channel',
      quantized_algorithm='normal',
      optimization_level=3,
    )
    rknn.load_onnx(model=str(WORK_ONNX), outputs=None)
    ret = rknn.build(do_quantization=True, dataset=str(dataset), auto_hybrid=True)
    print(f"  build (auto_hybrid): {ret}")

  if ret != 0:
    print("[FAIL] Both approaches failed", file=sys.stderr)
    sys.exit(1)

  rknn.export_rknn(str(rknn_path))
  with open(meta_path, "wb") as f:
    pickle.dump(metadata, f)

  size_mb = rknn_path.stat().st_size / 1e6
  print(f"\n[ok] {rknn_path.name}: {size_mb:.1f} MB")

  # Compare with FP16
  fp16_path = MODELS_DIR / "driving_vision.rknn"
  if fp16_path.exists():
    print(f"[ok] FP16 was: {fp16_path.stat().st_size/1e6:.1f} MB")

  rknn.release()
  print("\n[DONE] Benchmark on device: /tmp/bench_rknn <model.rknn>")

if __name__ == "__main__":
  main()
