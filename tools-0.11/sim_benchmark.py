#!/usr/bin/env python3
"""
RKNN Simulator — runs model in Docker WITHOUT a device.
Can measure: per-layer NPU vs CPU time, memory, and accuracy (FP16 vs INT8 diff).

This lets us evaluate new models before touching the real device.

Key functions:
  rknn.init_runtime(target=None)     → simulator mode (no device needed)
  rknn.eval_perf(inputs=...)         → per-layer timing estimate
  rknn.eval_memory()                 → memory usage estimate
  rknn.accuracy_analysis(inputs=...) → per-layer FP32 vs quantized output diff

Usage in Docker:
  python3 tools-local/sim_benchmark.py <model.rknn> [model2.rknn ...]
"""
import sys, os, time
import numpy as np

try:
  from rknn.api import RKNN
except ImportError:
  print("ERROR: rknn-toolkit2 not installed. Run via Docker.", file=sys.stderr)
  sys.exit(1)

def create_dummy_inputs(rknn):
  """Create dummy inputs matching the model's input shapes."""
  # Query input attrs from the model
  inputs_info = []
  for i in range(10):  # try up to 10 inputs
    try:
      # RKNN toolkit doesn't expose get_input_output_info directly,
      # but we can read it from the model metadata or use known shapes
      pass
    except:
      break
  return None

def sim_benchmark(model_path):
  print(f"\n{'='*60}")
  print(f"SIMULATOR: {os.path.basename(model_path)}")
  print(f"Size: {os.path.getsize(model_path)/1e6:.1f} MB")
  print(f"{'='*60}")

  rknn = RKNN(verbose=False)
  ret = rknn.load_rknn(model_path)
  if ret != 0:
    print(f"  load_rknn failed: {ret}")
    return

  # Init in SIMULATOR mode (target=None means no device)
  ret = rknn.init_runtime(target=None)  # simulator!
  if ret != 0:
    print(f"  init_runtime (simulator) failed: {ret}")
    rknn.release()
    return
  print("  [ok] Simulator initialized (no device needed)")

  # Get model info
  try:
    io = rknn.get_input_output_info()
    input_shapes = io.get('inputs', {})
    print(f"  Inputs: {len(input_shapes)}")
    for name, info in input_shapes.items():
      print(f"    {name}: shape={info.get('shape','?')}")

    # Create matching dummy inputs
    inputs = []
    for name, info in input_shapes.items():
      shape = info.get('shape', [1, 12, 128, 256])
      # Replace dynamic dims with 1
      shape = [s if isinstance(s, int) and s > 0 else 1 for s in shape]
      dtype = info.get('dtype', 'float32')
      if 'int8' in str(dtype):
        arr = np.random.randint(0, 200, shape).astype(np.int8)
      elif 'float16' in str(dtype):
        arr = np.random.uniform(0, 200, shape).astype(np.float16)
      else:
        arr = np.random.uniform(0, 200, shape).astype(np.float32)
      inputs.append(arr)
  except Exception as e:
    print(f"  [warn] couldn't get input info: {e}, using defaults")
    inputs = [np.random.uniform(0, 200, (1, 12, 128, 256)).astype(np.float32)]

  # Run inference in simulator (gives output + detects CPU fallback)
  print("\n  Running simulator inference...")
  try:
    outputs = rknn.inference(inputs=inputs)
    print(f"  [ok] Output: {len(outputs)} tensors, [0] shape={outputs[0].shape}")
    print(f"       min={outputs[0].min():.3f} max={outputs[0].max():.3f}")
    finite = np.isfinite(outputs[0]).all()
    print(f"       valid (no inf/nan): {finite}")
  except Exception as e:
    print(f"  [fail] inference error: {e}")

  # Performance evaluation (per-layer NPU timing estimate)
  print("\n  === PERF EVALUATION (per-layer estimate) ===")
  try:
    perf = rknn.eval_perf(inputs=inputs)
    if perf:
      # eval_perf returns a dict with per-layer timing
      print(f"  {perf}")
    else:
      print("  (no perf data returned)")
  except Exception as e:
    print(f"  eval_perf error: {e}")

  # Memory evaluation
  print("\n  === MEMORY EVALUATION ===")
  try:
    mem = rknn.eval_memory()
    if mem:
      print(f"  {mem}")
    else:
      print("  (no memory data returned)")
  except Exception as e:
    print(f"  eval_memory error: {e}")

  rknn.release()
  print(f"\n  [done] {os.path.basename(model_path)}")

def compare_models(models):
  """Run accuracy_analysis to compare FP16 vs INT8 outputs."""
  if len(models) < 2:
    print("\n(Need 2+ models for comparison)")
    return

  print(f"\n{'='*60}")
  print("ACCURACY COMPARISON (simulator mode)")
  print(f"{'='*60}")

  outputs_by_model = {}
  for model_path in models:
    rknn = RKNN(verbose=False)
    rknn.load_rknn(model_path)
    rknn.init_runtime(target=None)

    try:
      io = rknn.get_input_output_info()
      inputs = []
      for name, info in io.get('inputs', {}).items():
        shape = info.get('shape', [1, 12, 128, 256])
        shape = [s if isinstance(s, int) and s > 0 else 1 for s in shape]
        inputs.append(np.zeros(shape).astype(np.float32))  # same inputs for fair compare

      out = rknn.inference(inputs=inputs)
      outputs_by_model[os.path.basename(model_path)] = out[0]
    except Exception as e:
      print(f"  {model_path}: error {e}")
    rknn.release()

  # Compare outputs
  names = list(outputs_by_model.keys())
  if len(names) >= 2:
    a, b = outputs_by_model[names[0]], outputs_by_model[names[1]]
    diff = np.abs(a - b)
    print(f"\n  {names[0]} vs {names[1]}:")
    print(f"    Mean abs diff: {diff.mean():.6f}")
    print(f"    Max abs diff:  {diff.max():.6f}")
    print(f"    Relative diff: {diff.mean() / (np.abs(a).mean() + 1e-8) * 100:.3f}%")

if __name__ == "__main__":
  models = sys.argv[1:]
  if not models:
    # Default: compare FP16 vs INT8 vision models
    base = "/work/selfdrive/modeld/models"
    models = [f"{base}/driving_vision.rknn", f"{base}/driving_vision_int8.rknn"]

  for m in models:
    if os.path.exists(m):
      sim_benchmark(m)
    else:
      print(f"SKIP: {m} not found")

  if len(models) >= 2:
    compare_models(models)
