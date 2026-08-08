#!/usr/bin/env python3
"""
Exhaustive FP16 config testing for 0.11 supercombo.
Test every combination of optimization options to find max FPS.

Usage in Docker:
  python3 tools-local/sim_fp16_configs.py
"""
import sys, os, time, json, subprocess
import numpy as np

REPO = os.environ.get("REPO_ROOT_OVERRIDE", "/work")
ONNX = os.path.join(REPO, "selfdrive/modeld/models/driving_supercombo.onnx")
RESULTS_FILE = os.path.join(REPO, "tools-local/models_store/011-FP16-CONFIG-RESULTS.md")

SUPERCOMBO_INPUTS = [
    ('img', [1, 12, 128, 256]), ('big_img', [1, 12, 128, 256]),
    ('desire_pulse', [1, 25, 8]), ('traffic_convention', [1, 2]),
    ('action_t', [1, 2]), ('features_buffer', [1, 24, 512]),
]

def run_config(config_name, config_dict):
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

ret = rknn.build(do_quantization=False)
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
  err = result.stderr[-300:] if result.stderr else f"returncode={result.returncode}"
  return {"error": err}

def main():
  base = {
    "mean_values": [[]], "std_values": [[]],
    "target_platform": "rk3588", "optimization_level": 3,
    "float_dtype": "float16",
  }

  configs = [
    # 0: baseline
    ("FP16 baseline", {**base}),

    # 1: flash attention only
    ("FP16 + flash_attn", {**base, "enable_flash_attention": True}),

    # 2: remove reshape only
    ("FP16 + rm_reshape", {**base, "remove_reshape": True}),

    # 3: flash + reshape (best from previous test)
    ("FP16 + flash + rm_reshape", {**base, "enable_flash_attention": True, "remove_reshape": True}),

    # 4: flash + reshape + compress_weight
    ("FP16 + flash + rm_reshape + compress", {**base, "enable_flash_attention": True, "remove_reshape": True, "compress_weight": True}),

    # 5: flash + reshape + remove_weight
    ("FP16 + flash + rm_reshape + rm_weight", {**base, "enable_flash_attention": True, "remove_reshape": True, "remove_weight": True}),

    # 6: flash + reshape + model_pruning
    ("FP16 + flash + rm_reshape + pruning", {**base, "enable_flash_attention": True, "remove_reshape": True, "model_pruning": True}),

    # 7: flash + reshape + quantized_hybrid_level=1
    ("FP16 + flash + rm_reshape + hybrid_lvl1", {**base, "enable_flash_attention": True, "remove_reshape": True, "quantized_hybrid_level": 1}),

    # 8: optimization_level variations
    ("FP16 + flash + rm_reshape + opt_lvl2", {**base, "enable_flash_attention": True, "remove_reshape": True, "optimization_level": 2}),

    # 9: flash + reshape + single_core_mode
    ("FP16 + flash + rm_reshape + single_core", {**base, "enable_flash_attention": True, "remove_reshape": True, "single_core_mode": True}),

    # 10: everything on at once
    ("FP16 ALL optimizations", {**base, "enable_flash_attention": True, "remove_reshape": True, "compress_weight": True, "remove_weight": True}),

    # 11: custom_string for op_target (try forcing NPU for all ops)
    ("FP16 + flash + rm_reshape + op_target=npu", {**base, "enable_flash_attention": True, "remove_reshape": True, "op_target": "npu"}),
  ]

  all_results = []

  for name, config_dict in configs:
    # Extract just the changes for display
    changes = {k:v for k,v in config_dict.items() if k not in ('mean_values','std_values','target_platform','float_dtype')}
    changes_str = json.dumps(changes) if changes else "baseline"

    print(f"\n{'='*60}")
    print(f"  {name}")
    print(f"  changes: {changes_str}")
    print(f"{'='*60}")

    r = run_config(name, config_dict)
    r['name'] = name
    r['changes'] = changes_str

    if 'mean_ms' in r:
      hz = 1000 / r['mean_ms']
      print(f"  ✅ mean={r['mean_ms']}ms ({hz:.1f} Hz) valid={r['output_valid']}")
    else:
      print(f"  ❌ FAILED: {r.get('error', '?')[:80]}")

    all_results.append(r)
    save_results(all_results)

  # Summary sorted by speed
  print(f"\n\n{'='*90}")
  print(f"RESULTS SORTED BY SPEED (fastest first)")
  print(f"{'='*90}")
  successful = [r for r in all_results if 'mean_ms' in r]
  successful.sort(key=lambda x: x['mean_ms'])
  print(f"{'Config':<42} {'ms':>6} {'Hz':>6} {'Valid':>6}")
  print("-" * 65)
  for r in successful:
    hz = 1000 / r['mean_ms']
    print(f"{r['name']:<42} {r['mean_ms']:>5.1f} {hz:>5.1f} {str(r['output_valid']):>6}")

  failed = [r for r in all_results if 'error' in r]
  if failed:
    print(f"\nFailed configs:")
    for r in failed:
      print(f"  {r['name']}: {r['error'][:60]}")

  best = successful[0] if successful else None
  if best:
    baseline = next((r for r in all_results if r['name'] == 'FP16 baseline'), None)
    if baseline and 'mean_ms' in baseline:
      speedup = (baseline['mean_ms'] - best['mean_ms']) / baseline['mean_ms'] * 100
      print(f"\nBest: {best['name']} ({best['mean_ms']}ms) vs baseline ({baseline['mean_ms']}ms)")
      print(f"  {speedup:.1f}% faster")

  print(f"\nResults saved to {RESULTS_FILE}")

def save_results(results):
  with open(RESULTS_FILE, 'w') as f:
    f.write("# 0.11 Supercombo FP16 Config Testing\n")
    f.write(f"# Date: 2026-08-07\n")
    f.write(f"# Simulator: target=None (CPU). Same FP16 execution as device for relative comparison.\n\n")
    successful = sorted([r for r in results if 'mean_ms' in r], key=lambda x: x['mean_ms'])
    f.write(f"| Config | Sim ms | Sim Hz | Valid | Output range | Changes |\n")
    f.write(f"|--------|--------|--------|-------|-------------|---------|\n")
    for r in successful:
      hz = round(1000/r['mean_ms'], 1)
      f.write(f"| {r['name']} | {r['mean_ms']} | {hz} | {r['output_valid']} | [{r['output_min']}, {r['output_max']}] | {r['changes']} |\n")
    for r in results:
      if 'error' in r:
        f.write(f"| {r['name']} | FAIL | - | - | - | {r['error'][:40]} |\n")

if __name__ == "__main__":
  main()
