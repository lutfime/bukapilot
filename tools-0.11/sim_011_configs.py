#!/usr/bin/env python3
"""
Iterative config testing for 0.11 supercombo model.
Tests multiple RKNN config combinations, builds each, runs simulator benchmark,
saves results. Each config runs in a separate subprocess to avoid segfaults.

Usage in Docker:
  python3 tools-local/sim_011_configs.py
"""
import sys, os, time, json, subprocess
import numpy as np

REPO = os.environ.get("REPO_ROOT_OVERRIDE", "/work")
MODELS_DIR = os.path.join(REPO, "selfdrive", "modeld", "models")
RESULTS_FILE = os.path.join(REPO, "tools-local/models_store/011-CONFIG-RESULTS.md")

ONNX = os.path.join(MODELS_DIR, "driving_supercombo.onnx")
DATASET = os.path.join(REPO, "tools-local/calibration_frames/dataset.txt")

SUPERCOMBO_INPUTS = [
    ('img', [1, 12, 128, 256]), ('big_img', [1, 12, 128, 256]),
    ('desire_pulse', [1, 25, 8]), ('traffic_convention', [1, 2]),
    ('action_t', [1, 2]), ('features_buffer', [1, 24, 512]),
]

def run_config(config_name, dtype, config_dict, dataset_path=None):
  """Build and benchmark one config in a subprocess."""
  script = f'''
import sys, os, time, json
import numpy as np
from rknn.api import RKNN

rknn = RKNN(verbose=False)
rknn.config(**{config_dict})

ret = rknn.load_onnx(model="{ONNX}")
if ret != 0:
    print(json.dumps({{"error": f"load_onnx: {{ret}}"}}))
    sys.exit(0)

if "{dtype}" == "fp16":
    ret = rknn.build(do_quantization=False)
else:
    ret = rknn.build(do_quantization=True, dataset="{dataset_path or ""}")
if ret != 0:
    print(json.dumps({{"error": f"build: {{ret}}"}}))
    sys.exit(0)

ret = rknn.init_runtime(target=None)
if ret != 0:
    print(json.dumps({{"error": f"init_runtime: {{ret}}"}}))
    sys.exit(0)

inputs = []
for name, shape in {SUPERCOMBO_INPUTS}:
    if len(shape) == 4 and shape[0] == 1:
        nhwc = [1, shape[2], shape[3], shape[1]]
        inputs.append(np.random.uniform(0, 200, nhwc).astype(np.float32))
    elif len(shape) == 3:
        inputs.append(np.zeros(shape, dtype=np.float32))
    elif len(shape) == 2:
        inputs.append(np.array([[1.0, 0.0]], dtype=np.float32) if shape[1] == 2 else np.zeros(shape, dtype=np.float32))
    else:
        inputs.append(np.zeros(shape, dtype=np.float32))

for _ in range(3):
    try: rknn.inference(inputs=inputs, data_format="nhwc")
    except: rknn.inference(inputs=inputs)

times = []
for i in range(10):
    t0 = time.time()
    try: out = rknn.inference(inputs=inputs, data_format="nhwc")
    except: out = rknn.inference(inputs=inputs)
    times.append((time.time() - t0) * 1000)

times = np.array(times)
valid = bool(np.isfinite(out[0]).all()) if out else False
result = {{
    "mean_ms": round(float(times.mean()), 1),
    "median_ms": round(float(np.median(times)), 1),
    "min_ms": round(float(times.min()), 1),
    "max_ms": round(float(times.max()), 1),
    "output_valid": valid,
    "output_min": round(float(out[0].min()), 3) if out else 0,
    "output_max": round(float(out[0].max()), 3) if out else 0,
}}
print("RESULT:" + json.dumps(result))
rknn.release()
'''
  result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, timeout=600)
  for line in result.stdout.strip().split('\n'):
    if line.startswith("RESULT:"):
      return json.loads(line[7:])
  err = result.stderr[-200:] if result.stderr else f"returncode={result.returncode}"
  return {"error": err}

def main():
  # Define all configs to test
  base_config = {
    "mean_values": [[]], "std_values": [[]],
    "target_platform": "rk3588", "optimization_level": 3,
  }

  configs = [
    # Config 0: FP16 baseline (already known: ~80-90ms)
    ("FP16 baseline", "fp16", {**base_config}, None),

    # Config 1: FP16 + flash attention + remove reshape
    ("FP16 + flash_attn + rm_reshape", "fp16",
     {**base_config, "enable_flash_attention": True, "remove_reshape": True}, None),

    # Config 2: INT8 baseline (already known: ~85ms)
    ("INT8 baseline", "int8",
     {**base_config, "quantized_dtype": "w8a8", "quantized_method": "channel",
      "quantized_algorithm": "normal"}, DATASET),

    # Config 3: INT8 + flash attention + remove reshape
    ("INT8 + flash_attn + rm_reshape", "int8",
     {**base_config, "quantized_dtype": "w8a8", "quantized_method": "channel",
      "quantized_algorithm": "normal",
      "enable_flash_attention": True, "remove_reshape": True}, DATASET),

    # Config 4: INT8 + flash + compress_weight (smaller model)
    ("INT8 + flash + compress_weight", "int8",
     {**base_config, "quantized_dtype": "w8a8", "quantized_method": "channel",
      "quantized_algorithm": "normal",
      "enable_flash_attention": True, "compress_weight": True}, DATASET),

    # Config 5: INT8 + sparse_infer (sparse weight optimization)
    ("INT8 + sparse_infer", "int8",
     {**base_config, "quantized_dtype": "w8a8", "quantized_method": "channel",
      "quantized_algorithm": "normal", "sparse_infer": True}, DATASET),

    # Config 6: INT8 + model_pruning (remove unused weights)
    ("INT8 + model_pruning", "int8",
     {**base_config, "quantized_dtype": "w8a8", "quantized_method": "channel",
      "quantized_algorithm": "normal", "model_pruning": True}, DATASET),
  ]

  all_results = []

  for name, dtype, config_dict, dataset in configs:
    print(f"\n{'='*60}")
    print(f"  {name}")
    print(f"{'='*60}")
    print(f"  Building...", flush=True)

    r = run_config(name, dtype, config_dict, dataset)
    r['name'] = name
    r['dtype'] = dtype
    r['config'] = json.dumps({k: v for k, v in config_dict.items()
                              if k not in ('mean_values', 'std_values', 'target_platform', 'optimization_level')})

    if 'mean_ms' in r:
      hz = 1000 / r['mean_ms']
      print(f"  ✅ mean={r['mean_ms']}ms ({hz:.1f} Hz) valid={r['output_valid']}")
    else:
      print(f"  ❌ FAILED: {r.get('error', '?')[:80]}")

    all_results.append(r)

    # Save incrementally after each config
    save_results(all_results)

  # Final summary
  print(f"\n\n{'='*90}")
  print(f"{'Config':<35} {'Dtype':<6} {'Sim ms':>8} {'Sim Hz':>8} {'Valid':>6} {'Key changes'}")
  print("-" * 90)
  for r in all_results:
    name = r['name']
    dtype = r['dtype']
    ms = r.get('mean_ms', '-')
    valid = r.get('output_valid', '-')
    changes = r.get('config', '{}')
    if isinstance(ms, (int, float)):
      hz = f"{1000/ms:.1f}"
    else:
      hz = '-'
    if 'error' in r:
      print(f"{name:<35} {dtype:<6} {'FAIL':>8} {'':>8} {'':>6}  {r.get('error','')[:30]}")
    else:
      print(f"{name:<35} {dtype:<6} {str(ms):>8} {hz:>8} {str(valid):>6}  {changes}")

  print(f"\nResults saved to {RESULTS_FILE}")

def save_results(results):
  """Save results to markdown file."""
  with open(RESULTS_FILE, 'w') as f:
    f.write("# 0.11 Supercombo Config Testing Results\n")
    f.write(f"# Date: 2026-08-07\n")
    f.write(f"# Simulator: target=None (CPU). Cannot show INT8 speedup (no INT8 units on CPU).\n")
    f.write(f"# Purpose: Test which RKNN config options improve build success and output quality.\n")
    f.write(f"# Real speedup can only be measured on device NPU.\n\n")
    f.write(f"| Config | Dtype | Sim ms | Sim Hz | Valid | Output range | Config changes |\n")
    f.write(f"|--------|-------|--------|--------|-------|-------------|---------------|\n")
    for r in results:
      name = r['name']; dtype = r['dtype']
      if 'mean_ms' in r:
        hz = round(1000/r['mean_ms'], 1)
        outrange = f"[{r['output_min']}, {r['output_max']}]"
        changes = r.get('config', '{}')
        f.write(f"| {name} | {dtype} | {r['mean_ms']} | {hz} | {r['output_valid']} | {outrange} | {changes} |\n")
      else:
        f.write(f"| {name} | {dtype} | FAIL | - | - | - | {r.get('error','')[:40]} |\n")

if __name__ == "__main__":
  main()
