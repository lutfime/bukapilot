#!/usr/bin/env python3
"""
RKNN Simulator — benchmark ALL models. Each model runs in a separate subprocess
to prevent segfaults from killing the whole run.

Usage in Docker:
  python3 tools-local/sim_benchmark_all.py
"""
import sys, os, time, json, subprocess
import numpy as np

def run_single(model_name, onnx_path, dtype, dataset_path, input_config):
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
print(f"load_onnx: {{ret}}", flush=True)
if ret != 0: sys.exit(1)

if dtype == "fp16":
    ret = rknn.build(do_quantization=False)
else:
    ret = rknn.build(do_quantization=True, dataset=dataset)
print(f"build: {{ret}}", flush=True)
if ret != 0: sys.exit(1)

ret = rknn.init_runtime(target=None)
print(f"init_runtime: {{ret}}", flush=True)
if ret != 0: sys.exit(1)

# Create inputs in NHWC
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

# Warmup
for _ in range(3):
    try:
        rknn.inference(inputs=inputs, data_format="nhwc")
    except:
        rknn.inference(inputs=inputs)

# Benchmark
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
  # Parse output
  for line in result.stdout.strip().split('\n'):
    if line.startswith("RESULT:"):
      return json.loads(line[7:])
  # Failed
  return {"error": result.stderr[-200:] if result.stderr else "no result", "returncode": result.returncode}

def main():
  REPO = os.environ.get("REPO_ROOT_OVERRIDE", "/work")
  MODELS_DIR = os.path.join(REPO, "selfdrive", "modeld", "models")

  VISION_INPUTS = [('img', [1, 12, 128, 256]), ('big_img', [1, 12, 128, 256])]
  POLICY_INPUTS = [('desire_pulse', [1, 25, 8]), ('traffic_convention', [1, 2]), ('features_buffer', [1, 25, 512])]
  SUPERCOMBO_INPUTS = [
    ('img', [1, 12, 128, 256]), ('big_img', [1, 12, 128, 256]),
    ('desire_pulse', [1, 25, 8]), ('traffic_convention', [1, 2]),
    ('action_t', [1, 2]), ('features_buffer', [1, 24, 512]),
  ]

  DATASET_011 = os.path.join(REPO, "tools-local/calibration_frames/dataset.txt")
  DATASET_VISION = os.path.join(REPO, "tools-local/calibration_frames/dataset_vision.txt")

  VISION_ONNX = os.path.join(MODELS_DIR, "driving_vision_fixed.onnx")
  POLICY_ONNX = os.path.join(MODELS_DIR, "driving_policy.onnx")
  SUPERCOMBO_ONNX = os.path.join(MODELS_DIR, "driving_supercombo.onnx")

  tests = [
    ("0.10 vision FP16", VISION_ONNX, 'fp16', None, VISION_INPUTS),
    ("0.10 vision INT8", VISION_ONNX, 'int8', DATASET_VISION, VISION_INPUTS),
    ("0.10 policy FP16", POLICY_ONNX, 'fp16', None, POLICY_INPUTS),
    ("0.11 supercombo FP16", SUPERCOMBO_ONNX, 'fp16', None, SUPERCOMBO_INPUTS),
    ("0.11 supercombo INT8", SUPERCOMBO_ONNX, 'int8', DATASET_011, SUPERCOMBO_INPUTS),
  ]

  all_results = []
  for name, onnx, dtype, dataset, inputs in tests:
    if not os.path.exists(onnx):
      print(f"SKIP {name}: ONNX not found ({onnx})")
      all_results.append({"name": name, "dtype": dtype, "error": "onnx not found"})
      continue
    if dtype == 'int8' and dataset and not os.path.exists(dataset):
      print(f"SKIP {name}: dataset not found ({dataset})")
      all_results.append({"name": name, "dtype": dtype, "error": "dataset not found"})
      continue

    print(f"\n{'='*60}")
    print(f"  {name}")
    print(f"{'='*60}")
    r = run_single(name, onnx, dtype, dataset, inputs)
    r['name'] = name
    r['dtype'] = dtype
    all_results.append(r)

    if 'mean_ms' in r:
      print(f"  mean={r['mean_ms']}ms median={r['median_ms']}ms valid={r['output_valid']}")
    else:
      print(f"  FAILED: {r.get('error', '?')[:100]}")

  # Print summary
  print(f"\n\n{'='*80}")
  print("SIMULATOR RESULTS SUMMARY")
  print(f"{'='*80}")
  print(f"{'Model':<28} {'Dtype':<6} {'Sim Mean':>10} {'Sim Med':>10} {'Valid':>6}")
  print("-" * 80)
  for r in all_results:
    name = r['name']
    dtype = r['dtype']
    mean = r.get('mean_ms', '-')
    med = r.get('median_ms', '-')
    valid = r.get('output_valid', '-')
    if 'error' in r:
      print(f"{name:<28} {dtype:<6} {'FAIL':>10} {'':>10} {'':>6}  {r['error'][:30]}")
    else:
      print(f"{name:<28} {dtype:<6} {str(mean):>10} {str(med):>10} {str(valid):>6}")

  # Device comparison
  print(f"\n{'='*80}")
  print("DEVICE vs SIMULATOR COMPARISON")
  print(f"{'='*80}")
  print(f"{'Model':<28} {'Device C API':>14} {'Simulator':>12} {'Ratio':>8}")
  print("-" * 80)
  device_known = {
    "0.10 vision FP16": 29.7,
    "0.10 policy FP16": 3.1,
    "0.11 supercombo INT8": 79.5,
  }
  for r in all_results:
    name = r['name']
    dev = device_known.get(name, '-')
    sim = r.get('mean_ms', '-')
    if isinstance(dev, float) and isinstance(sim, float):
      ratio = round(sim / dev, 2)
      print(f"{name:<28} {dev:>13.1f}ms {sim:>11.1f}ms {ratio:>7.2f}x")
    elif isinstance(sim, float):
      print(f"{name:<28} {'?':>14} {sim:>11.1f}ms {'?':>8}")
    else:
      print(f"{name:<28} {str(dev):>14} {str(sim):>12} {'?':>8}")

  # Save results
  results_file = os.path.join(REPO, "tools-local/models_store/SIMULATOR-RESULTS.md")
  with open(results_file, 'w') as f:
    f.write("# RKNN Simulator Results (all models)\n")
    f.write(f"# Date: 2026-08-07\n")
    f.write(f"# Simulator: target=None (CPU, no device). eval_perf/eval_memory not supported in simulator.\n")
    f.write(f"# Simulator timing is CPU-based — NOT NPU timing. Relative differences are meaningful.\n\n")
    f.write(f"## Simulator results\n\n")
    f.write(f"| Model | Dtype | Sim Mean (ms) | Sim Median (ms) | Sim Min | Sim Max | Output Valid |\n")
    f.write(f"|-------|-------|---------------|-----------------|---------|---------|-------------|\n")
    for r in all_results:
      name = r['name']; dtype = r['dtype']
      if 'mean_ms' in r:
        f.write(f"| {name} | {dtype} | {r['mean_ms']} | {r['median_ms']} | {r['min_ms']} | {r['max_ms']} | {r['output_valid']} |\n")
      else:
        f.write(f"| {name} | {dtype} | FAIL | - | - | - | - |\n")

    f.write(f"\n## Device vs Simulator comparison\n\n")
    f.write(f"| Model | Device C API (ms) | Simulator (ms) | Sim/Device ratio |\n")
    f.write(f"|-------|-------------------|----------------|-----------------|\n")
    for r in all_results:
      name = r['name']
      dev = device_known.get(name)
      sim = r.get('mean_ms')
      if dev and sim:
        f.write(f"| {name} | {dev} | {sim} | {round(sim/dev,2)}x |\n")
      elif sim:
        f.write(f"| {name} | ? | {sim} | ? |\n")
      else:
        f.write(f"| {name} | {dev or '?'} | FAIL | ? |\n")
  print(f"\nResults saved to {results_file}")

if __name__ == "__main__":
  main()
