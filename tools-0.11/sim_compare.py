#!/usr/bin/env python3
"""
RKNN Simulator — builds model from ONNX and evaluates WITHOUT a device.

The simulator requires building from ONNX in the same session (cannot load_rknn).
This script:
  1. Loads ONNX, builds with FP16 or INT8
  2. Runs inference in simulator mode (target=None)
  3. Reports per-layer perf estimate, memory, and output values

Usage in Docker:
  # FP16 vs INT8 comparison:
  python3 tools-local/sim_compare.py

  # Custom:
  python3 tools-local/sim_compare.py <onnx_path> <dtype> [dataset.txt]
  # dtype: fp16, int8
"""
import sys, os, time, glob, pickle, base64
import numpy as np
from PIL import Image

try:
  from rknn.api import RKNN
except ImportError:
  print("ERROR: rknn-toolkit2 not installed. Run via Docker.", file=sys.stderr)
  sys.exit(1)

REPO_ROOT = os.environ.get("REPO_ROOT_OVERRIDE", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if not os.path.isabs(REPO_ROOT):
  REPO_ROOT = os.path.abspath(REPO_ROOT)
MODELS_DIR = os.path.join(REPO_ROOT, "selfdrive", "modeld", "models")
CALIB_DIR = os.path.join(REPO_ROOT, "tools-local", "calibration_frames")

def load_vision_inputs(n=5):
  """Load real calibration frames as vision model inputs."""
  extracted = os.path.join(CALIB_DIR, "extracted")
  if not os.path.isdir(extracted):
    print("  [warn] no calibration frames, using random data")
    return [np.random.uniform(0, 200, (1, 12, 128, 256)).astype(np.float32),
            np.random.uniform(0, 200, (1, 12, 128, 256)).astype(np.float32)]

  # Find fcamera + ecamera pairs
  segments = {}
  for d in sorted(glob.glob(os.path.join(extracted, "*"))):
    seg = os.path.basename(d)
    base = seg.rsplit("_", 1)[0]
    cam = seg.rsplit("_", 1)[1]
    segments.setdefault(base, {})
    for f in sorted(glob.glob(os.path.join(d, "frame_*.jpg")))[:1]:
      segments[base][cam] = f

  pairs = [(v['fcamera'], v['ecamera']) for v in segments.values()
           if 'fcamera' in v and 'ecamera' in v][:n]
  if not pairs:
    return [np.random.uniform(0, 200, (1, 12, 128, 256)).astype(np.float32)] * 2

  def transform(path):
    im = Image.open(path).convert("L").resize((256, 128), Image.BILINEAR)
    arr = np.asarray(im, dtype=np.float32)
    return np.tile(arr.reshape(1, 1, 128, 256), (1, 12, 1, 1))

  fcam = transform(pairs[0][0])
  ecam = transform(pairs[0][1])
  return [fcam, ecam]

def build_and_simulate(onnx_path, dtype='fp16', dataset_path=None):
  """Build model from ONNX and run in simulator."""
  dtype_label = "FP16" if dtype == 'fp16' else "INT8"
  print(f"\n{'='*60}")
  print(f"BUILD + SIMULATE: {os.path.basename(onnx_path)} ({dtype_label})")
  print(f"{'='*60}")

  rknn = RKNN(verbose=False)

  if dtype == 'fp16':
    rknn.config(
      mean_values=[[]], std_values=[[]],
      target_platform="rk3588",
      optimization_level=3,
    )
  else:
    rknn.config(
      mean_values=[[]], std_values=[[]],
      target_platform="rk3588",
      quantized_dtype='w8a8',
      quantized_method='channel',
      quantized_algorithm='normal',
      optimization_level=3,
    )

  ret = rknn.load_onnx(model=onnx_path)
  print(f"  load_onnx: {ret}")
  if ret != 0:
    rknn.release(); return None

  if dtype == 'fp16':
    ret = rknn.build(do_quantization=False)
  else:
    ret = rknn.build(do_quantization=True, dataset=dataset_path)
  print(f"  build ({dtype_label}): {ret}")
  if ret != 0:
    rknn.release(); return None

  # SIMULATOR MODE — target='rk3588' simulates the NPU on PC (no device needed)
  ret = rknn.init_runtime(target='rk3588')
  print(f"  init_runtime (simulator target=rk3588): {ret}")
  if ret != 0:
    rknn.release(); return None

  # Create inputs
  inputs = load_vision_inputs()

  # Run inference
  print("\n  Running simulator inference...")
  t0 = time.time()
  outputs = rknn.inference(inputs=inputs)
  sim_time = time.time() - t0
  print(f"  Simulator inference: {sim_time*1000:.1f}ms (CPU timing, NOT NPU)")
  print(f"  Output: shape={outputs[0].shape}, min={outputs[0].min():.3f}, max={outputs[0].max():.3f}")
  finite = np.isfinite(outputs[0]).all()
  print(f"  Valid (no inf/nan): {finite}")

  # Performance evaluation — per-layer NPU timing estimate
  print("\n  === PERF EVALUATION (NPU timing estimate) ===")
  try:
    rknn.eval_perf(inputs=inputs)
  except Exception as e:
    print(f"  eval_perf: {e}")

  # Memory evaluation
  print("\n  === MEMORY EVALUATION ===")
  try:
    rknn.eval_memory()
  except Exception as e:
    print(f"  eval_memory: {e}")

  rknn.release()
  return outputs[0]

def main():
  onnx_path = os.path.join(MODELS_DIR, "driving_vision.onnx")
  if len(sys.argv) > 1:
    onnx_path = sys.argv[1]

  if not os.path.exists(onnx_path):
    print(f"[FAIL] ONNX not found: {onnx_path}")
    sys.exit(1)

  # Always use the fixed ONNX (zero-size fix + UINT8→FLOAT cast applied)
  fixed_onnx = os.path.join(MODELS_DIR, "driving_vision_fixed.onnx")
  if not os.path.exists(fixed_onnx):
    print("[FAIL] Fixed ONNX not found. Run the fix step first.")
    sys.exit(1)

  dataset = os.path.join(CALIB_DIR, "dataset_vision.txt")

  # FP16 (uses fixed ONNX too — needs UINT8→FLOAT cast for build)
  fp16_out = build_and_simulate(fixed_onnx, dtype='fp16')

  # INT8
  int8_out = build_and_simulate(fixed_onnx, dtype='int8', dataset_path=dataset)

  # Compare outputs
  if fp16_out is not None and int8_out is not None:
    print(f"\n{'='*60}")
    print("ACCURACY COMPARISON: FP16 vs INT8 (same inputs)")
    print(f"{'='*60}")
    diff = np.abs(fp16_out - int8_out)
    print(f"  Mean abs diff: {diff.mean():.6f}")
    print(f"  Max abs diff:  {diff.max():.6f}")
    print(f"  FP16 range: [{fp16_out.min():.3f}, {fp16_out.max():.3f}]")
    print(f"  INT8 range: [{int8_out.min():.3f}, {int8_out.max():.3f}]")
    rel_diff = diff.mean() / (np.abs(fp16_out).mean() + 1e-8) * 100
    print(f"  Relative diff: {rel_diff:.3f}%")
    print(f"\n  Interpretation:")
    print(f"    <0.5%  → imperceptible, safe for driving")
    print(f"    0.5-2% → small drift, test drive needed")
    print(f"    >2%    → significant, likely degrades driving quality")

if __name__ == "__main__":
  main()
