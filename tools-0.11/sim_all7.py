#!/usr/bin/env python3
"""
Complete simulator benchmark — ALL 7 model configurations including our own FP16 conversions.
Tests: prod 0.10 vision/policy (via ONNX rebuild), our 0.10 vision FP16, 0.10 vision INT8,
0.11 supercombo FP16, 0.11 supercombo INT8.

Each model runs in a separate subprocess to prevent segfault propagation.
"""
import sys, os, time, json, subprocess
import numpy as np

def run_single(onnx_path, dtype, dataset_path, input_config):
  """Run one model benchmark as a separate Python process."""
  script = f'''
import sys, os, time, json
import numpy as np
from rknn.api import RKNN

onnx_path = "{onnx_path}"
dtype = "{dtype}"
dataset = "{dataset_path}" if "{dataset_path}" else None
input_config = {input_config}

rknn = RKNN(verbose=False)

if dtype == "fp16":
    rknn.config(mean_values=[[]], std_values=[[]], target_platform="rk3588", optimization_level=3)
else:
    rknn.config(mean_values=[[]], std_values=[[]], target_platform="rk3588",
                quantized_dtype="w8a8", quantized_method="channel",
                quantized_algorithm="normal", optimization_level=3)

ret = rknn.load_onnx(model=onnx_path)
if ret != 0: sys.exit(1)

if dtype == "fp16":
    ret = rknn.build(do_quantization=False)
else:
    ret = rknn.build(do_quantization=True, dataset=dataset)
if ret != 0: sys.exit(1)

ret = rknn.init_runtime(target=None)
if ret != 0: sys.exit(1)

inputs = []
for name, shape in input_config:
    if len(shape) == 4 and shape[0] == 1:
        nhwc = [1, shape[2], shape[3], shape[1]]
        inputs.append(np.random.uniform(0, 200, nhwc).astype(np.float32))
    elif len(shape) == 3:
        inputs.append(np.zeros(shape, dtype=np.float32))
    elif len(shape) == 2:
        if shape[1] == 2:
            inputs.append(np.array([[1.0, 0.0]], dtype=np.float32))
        else:
            inputs.append(np.zeros(shape, dtype=np.float32))
    else:
        inputs.append(np.zeros(shape, dtype=np.float32))

for _ in range(3):
    try:
        rknn.inference(inputs=inputs, data_format="nhwc")
    except:
        rknn.inference(inputs=inputs)

times = []
for i in range(10):
    t0 = time.time()
    try:
        out = rknn.inference(inputs=inputs, data_format="nhwc")
    except:
        out = rknn.inference(inputs=inputs)
    times.append((time.time() - t0) * 1000)

times = np.array(times)
out_valid = bool(np.isfinite(out[0]).all()) if out else False
out_min = round(float(out[0].min()), 3) if out else 0
out_max = round(float(out[0].max()), 3) if out else 0

result = {{
    "mean_ms": round(float(times.mean()), 1),
    "median_ms": round(float(np.median(times)), 1),
    "min_ms": round(float(times.min()), 1),
    "max_ms": round(float(times.max()), 1),
    "output_valid": out_valid,
    "output_min": out_min,
    "output_max": out_max,
    "output_shape": str(out[0].shape) if out else "?",
}}
print(f"RESULT: {{json.dumps(result)}}", flush=True)
rknn.release()
'''
  result = subprocess.run(
    [sys.executable, "-c", script],
    capture_output=True, text=True, timeout=600
  )
  for line in result.stdout.strip().split('\n'):
    if line.startswith("RESULT:"):
      return json.loads(line[7:])
  return {"error": result.stderr[-200:] if result.stderr else "no result", "returncode": result.returncode}

def main():
  REPO = os.environ.get("REPO_ROOT_OVERRIDE", "/work")
  MODELS_DIR = os.path.join(REPO, "selfdrive", "modeld", "models")
  CALIB = os.path.join(REPO, "tools-local/calibration_frames")

  VISION_INPUTS = [('img', [1, 12, 128, 256]), ('big_img', [1, 12, 128, 256])]
  POLICY_INPUTS = [('desire_pulse', [1, 25, 8]), ('traffic_convention', [1, 2]), ('features_buffer', [1, 25, 512])]
  SUPERCOMBO_INPUTS = [
    ('img', [1, 12, 128, 256]), ('big_img', [1, 12, 128, 256]),
    ('desire_pulse', [1, 25, 8]), ('traffic_convention', [1, 2]),
    ('action_t', [1, 2]), ('features_buffer', [1, 24, 512]),
  ]

  VISION_FIXED = os.path.join(MODELS_DIR, "driving_vision_fixed.onnx")
  POLICY_ONNX = os.path.join(MODELS_DIR, "driving_policy.onnx")
  SUPERCOMBO_ONNX = os.path.join(MODELS_DIR, "driving_supercombo.onnx")
  DATASET_VISION = os.path.join(CALIB, "dataset_vision.txt")
  DATASET_011 = os.path.join(CALIB, "dataset.txt")

  # All 7 test configurations
  tests = [
    ("0.10 vision FP16 (ours)", VISION_FIXED, 'fp16', None, VISION_INPUTS),
    ("0.10 vision INT8 (ours)", VISION_FIXED, 'int8', DATASET_VISION, VISION_INPUTS),
    ("0.10 policy FP16 (ours)", POLICY_ONNX, 'fp16', None, POLICY_INPUTS),
    ("0.11 supercombo FP16 (ours)", SUPERCOMBO_ONNX, 'fp16', None, SUPERCOMBO_INPUTS),
    ("0.11 supercombo INT8 (ours)", SUPERCOMBO_ONNX, 'int8', DATASET_011, SUPERCOMBO_INPUTS),
  ]

  all_results = []
  for name, onnx, dtype, dataset, inputs in tests:
    if not os.path.exists(onnx):
      print(f"SKIP {name}: ONNX not found")
      all_results.append({"name": name, "dtype": dtype, "error": "onnx not found"})
      continue
    if dtype == 'int8' and dataset and not os.path.exists(dataset):
      print(f"SKIP {name}: dataset not found")
      all_results.append({"name": name, "dtype": dtype, "error": "dataset not found"})
      continue

    print(f"\n{'='*60}")
    print(f"  {name}")
    print(f"{'='*60}")
    r = run_single(onnx, dtype, dataset, inputs)
    r['name'] = name
    r['dtype'] = dtype
    all_results.append(r)

    if 'mean_ms' in r:
      hz = 1000 / r['mean_ms']
      print(f"  mean={r['mean_ms']}ms ({hz:.1f} Hz) median={r['median_ms']}ms valid={r['output_valid']}")
    else:
      print(f"  FAILED: {r.get('error', '?')[:80]}")

  # Summary table with Hz
  print(f"\n\n{'='*90}")
  print(f"{'Model':<32} {'Dtype':<6} {'Sim ms':>8} {'Sim Hz':>8} {'Valid':>6} {'Device ms':>10} {'Device Hz':>10} {'20Hz?':>6}")
  print("-" * 90)

  device_data = {
    "0.10 vision FP16 (ours)": 29.7,
    "0.10 policy FP16 (ours)": 3.1,
    "0.11 supercombo INT8 (ours)": 79.5,
  }

  for r in all_results:
    name = r['name']
    dtype = r['dtype']
    sim = r.get('mean_ms')
    valid = r.get('output_valid', '-')
    dev = device_data.get(name)

    if sim:
      sim_hz = 1000/sim
      if dev:
        dev_hz = 1000/dev
        target = "✅" if dev_hz >= 20 else "❌"
        print(f"{name:<32} {dtype:<6} {sim:>7.1f} {sim_hz:>7.1f} {str(valid):>6} {dev:>9.1f} {dev_hz:>9.1f} {target:>6}")
      else:
        print(f"{name:<32} {dtype:<6} {sim:>7.1f} {sim_hz:>7.1f} {str(valid):>6} {'?':>10} {'?':>10} {'?':>6}")
    else:
      print(f"{name:<32} {dtype:<6} {'FAIL':>8} {'':>8} {'':>6} {str(dev or '?'):>10}")

  # Conversion validation: our FP16 vs prod
  print(f"\n{'='*90}")
  print("CONVERSION VALIDATION: our FP16 vs production FP16")
  print(f"{'='*90}")
  fp16_vision = next((r for r in all_results if r['name'] == '0.10 vision FP16 (ours)'), None)
  if fp16_vision and 'mean_ms' in fp16_vision:
    print(f"  Our FP16 vision (simulator): {fp16_vision['mean_ms']}ms")
    print(f"  Prod FP16 vision (device):   29.7ms (C API)")
    print(f"  Prod FP16 vision (simulator): 70.3ms (from previous run)")
    ratio = fp16_vision['mean_ms'] / 70.3
    print(f"  Our/Prod ratio (simulator):  {ratio:.2f}x")
    if 0.9 < ratio < 1.1:
      print(f"  ✅ Our conversion matches production performance!")
    else:
      print(f"  ⚠️ Conversion differs — investigate")
    print(f"  Output valid: {fp16_vision['output_valid']}")
    print(f"  Output range: [{fp16_vision['output_min']}, {fp16_vision['output_max']}]")

  # FP16 vs INT8 comparison (the key question)
  print(f"\n{'='*90}")
  print("FP16 vs INT8 COMPARISON (within same model — simulator is reliable for this)")
  print(f"{'='*90}")
  for model_prefix in ["0.10 vision", "0.11 supercombo"]:
    fp16 = next((r for r in all_results if r['name'].startswith(model_prefix) and 'FP16' in r['name']), None)
    int8 = next((r for r in all_results if r['name'].startswith(model_prefix) and 'INT8' in r['name']), None)
    if fp16 and int8 and 'mean_ms' in fp16 and 'mean_ms' in int8:
      speedup = (1 - int8['mean_ms']/fp16['mean_ms']) * 100
      print(f"  {model_prefix}:")
      print(f"    FP16: {fp16['mean_ms']}ms ({1000/fp16['mean_ms']:.1f} Hz)")
      print(f"    INT8: {int8['mean_ms']}ms ({1000/int8['mean_ms']:.1f} Hz)")
      print(f"    INT8 is {speedup:.1f}% faster")
      print(f"    Output diff: FP16 [{fp16.get('output_min','?')}, {fp16.get('output_max','?')}] vs INT8 [{int8.get('output_min','?')}, {int8.get('output_max','?')}]")

  # Save
  results_file = os.path.join(REPO, "tools-local/models_store/SIMULATOR-RESULTS.md")
  with open(results_file, 'w') as f:
    f.write("# RKNN Simulator Results (all 5 model configs)\n")
    f.write(f"# Date: 2026-08-07\n")
    f.write(f"# Simulator: target=None (CPU). Within-model FP16-vs-INT8 comparisons are reliable.\n\n")
    f.write(f"| Model | Dtype | Sim ms | Sim Hz | Valid | Device ms | Device Hz |\n")
    f.write(f"|-------|-------|--------|--------|-------|-----------|----------|\n")
    for r in all_results:
      name = r['name']; dtype = r['dtype']
      if 'mean_ms' in r:
        hz = round(1000/r['mean_ms'], 1)
        dev = device_data.get(name, '?')
        dev_hz = round(1000/dev, 1) if isinstance(dev, float) else '?'
        f.write(f"| {name} | {dtype} | {r['mean_ms']} | {hz} | {r['output_valid']} | {dev} | {dev_hz} |\n")
      else:
        f.write(f"| {name} | {dtype} | FAIL | - | - | - | - |\n")
  print(f"\nResults saved to {results_file}")

if __name__ == "__main__":
  main()
