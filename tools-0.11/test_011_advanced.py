#!/usr/bin/env python3
"""
Two approaches to improve 0.11 supercombo:

1. MMSE algorithm - better quantization (needs more RAM)
2. Graph surgery - rewrite problematic ONNX ops to eliminate CPU fallback

Usage in Docker:
  python3 tools-local/test_011_advanced.py
"""
import sys, os, time, json, subprocess, copy
import numpy as np
import onnx
from onnx import helper, TensorProto

REPO = os.environ.get("REPO_ROOT_OVERRIDE", "/work")
ONNX_PATH = os.path.join(REPO, "selfdrive/modeld/models/driving_supercombo.onnx")
DATASET = os.path.join(REPO, "tools-local/calibration_frames/dataset.txt")
RESULTS_FILE = os.path.join(REPO, "tools-local/models_store/011-ADVANCED-RESULTS.md")

SUPERCOMBO_INPUTS = [
    ('img', [1, 12, 128, 256]), ('big_img', [1, 12, 128, 256]),
    ('desire_pulse', [1, 25, 8]), ('traffic_convention', [1, 2]),
    ('action_t', [1, 2]), ('features_buffer', [1, 24, 512]),
]

def analyze_graph(onnx_path):
  """Find ops that likely fall back to CPU."""
  m = onnx.load(onnx_path)
  npu_ops = {'Conv', 'MatMul', 'Add', 'Sub', 'Mul', 'Div', 'Relu', 'Sigmoid',
             'Tanh', 'Softmax', 'Concat', 'Reshape', 'Transpose', 'ReduceMean',
             'LayerNormalization', 'Gemm', 'GlobalAveragePool', 'Pad', 'Slice', 'Erf',
             'Constant', 'Pow', 'Sqrt', 'Exp', 'Cast'}
  fallback = {}
  for node in m.graph.node:
    if node.op_type not in npu_ops:
      fallback[node.op_type] = fallback.get(node.op_type, 0) + 1
  return fallback

def eliminate_squeeze_unsqueeze_pairs(onnx_path, output_path):
  """
  Remove Squeeze-Unsqueeze pairs that cancel each other out.
  Pattern: X -> Squeeze -> Unsqueeze -> Y  (net effect: identity)
  Also remove standalone Squeeze ops where axes are known constants.
  """
  m = onnx.load(onnx_path)

  # Build a map of node outputs -> node
  output_to_node = {}
  for node in m.graph.node:
    for out in node.output:
      output_to_node[out] = node

  # Find Squeeze-Unsqueeze pairs
  nodes_to_remove = set()
  bypass = {}  # input -> replacement output

  for node in m.graph.node:
    if node.op_type == 'Squeeze':
      squeeze_out = node.output[0]
      squeeze_in = node.input[0]
      # Check if any Unsqueeze consumes this Squeeze's output
      for other in m.graph.node:
        if other.op_type == 'Unsqueeze' and other.input[0] == squeeze_out:
          # Found a pair: squeeze -> unsqueeze = identity
          unsqueeze_out = other.output[0]
          # Bypass: anything using unsqueeze_out should use squeeze_in instead
          bypass[unsqueeze_out] = squeeze_in
          nodes_to_remove.add(node.name)
          nodes_to_remove.add(other.name)
          print(f"  Eliminate pair: {node.name} -> {other.name} (bypass {unsqueeze_out} → {squeeze_in})")
          break

  if not bypass:
    print("  No Squeeze-Unsqueeze pairs found")
    return False

  # Apply bypasses to all nodes
  for node in m.graph.node:
    new_inputs = []
    for inp in node.input:
      new_inputs.append(bypass.get(inp, inp))
    del node.input[:]
    node.input.extend(new_inputs)

  # Also fix graph outputs
  for i, out in enumerate(m.graph.output):
    if out.name in bypass:
      out.name = bypass[out.name]

  # Remove the paired nodes
  new_nodes = [n for n in m.graph.node if n.name not in nodes_to_remove]
  del m.graph.node[:]
  m.graph.node.extend(new_nodes)

  onnx.save(m, output_path)
  print(f"  Removed {len(nodes_to_remove)} nodes, saved to {output_path}")
  return True

def replace_where_with_select(onnx_path, output_path):
  """
  Where ops can sometimes be replaced with Select (more NPU-friendly).
  Where(condition, X, Y) → if condition is a simple comparison, can use Cast+Mul+Add.
  """
  m = onnx.load(onnx_path)
  where_count = sum(1 for n in m.graph.node if n.op_type == 'Where')
  print(f"  Found {where_count} Where ops")
  # Where is actually NPU-supported in newer RKNN - just flag it
  # Don't modify for now, just note it
  onnx.save(m, output_path)
  return False

def remove_unused_casts(onnx_path, output_path):
  """Remove Cast ops that convert to the same dtype."""
  m = onnx.load(onnx_path)
  # Cast is usually harmless - skip for now
  onnx.save(m, output_path)
  return False

def run_sim_benchmark(name, onnx_path, dtype, config_extra="", dataset=None):
  """Run simulator benchmark for a model."""
  extra_dict = {}
  if config_extra:
    for item in config_extra.split(","):
      k, v = item.strip().split("=")
      if v == "True": v = True
      elif v == "False": v = False
      extra_dict[k] = v

  config_str = json.dumps({
    "mean_values": [[]], "std_values": [[]],
    "target_platform": "rk3588", "optimization_level": 3,
    "float_dtype": "float16",
    **extra_dict
  })

  script = f'''
import sys, os, time, json
import numpy as np
from rknn.api import RKNN

config = {config_str}
rknn = RKNN(verbose=False)
rknn.config(**config)

ret = rknn.load_onnx(model="{onnx_path}")
if ret != 0:
    print(json.dumps({{"error": f"load_onnx: {{ret}}"}}))
    sys.exit(0)

if "{dtype}" == "fp16":
    ret = rknn.build(do_quantization=False)
else:
    ret = rknn.build(do_quantization=True, dataset="{dataset or ""}", )
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
    "valid": valid,
    "output_min": round(float(out[0].min()), 3) if out else 0,
    "output_max": round(float(out[0].max()), 3) if out else 0,
}}
print("RESULT:" + json.dumps(result))
rknn.release()
'''
  result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, timeout=600)
  for line in result.stdout.strip().split('\n'):
    if line.startswith("RESULT:"):
      r = json.loads(line[7:])
      r['name'] = name
      return r
  err = result.stderr[-200:] if result.stderr else f"exit={result.returncode}"
  return {"name": name, "error": err}

def main():
  all_results = []

  print("=" * 60)
  print("STEP 1: Analyze original graph")
  print("=" * 60)
  fallback = analyze_graph(ONNX_PATH)
  print(f"Original CPU fallback ops: {fallback}")

  # --- Graph surgery ---
  print("\n" + "=" * 60)
  print("STEP 2: Graph surgery — eliminate Squeeze/Unsqueeze pairs")
  print("=" * 60)
  surg_path = os.path.join(REPO, "selfdrive/modeld/models/driving_supercombo_surg.onnx")
  changed1 = eliminate_squeeze_unsqueeze_pairs(ONNX_PATH, surg_path)

  if changed1:
    surg_fallback = analyze_graph(surg_path)
    print(f"After surgery CPU fallback ops: {surg_fallback}")

  # --- Test configs ---
  print("\n" + "=" * 60)
  print("STEP 3: Benchmark configs")
  print("=" * 60)

  tests = [
    # FP16 tests
    ("A1: FP16 baseline (original)", ONNX_PATH, "fp16", "", None),
    ("A2: FP16 + flash + rm_reshape (original)", ONNX_PATH, "fp16",
     "enable_flash_attention=True,remove_reshape=True", None),
    ("A3: FP16 + flash + rm_reshape (surgery)", surg_path, "fp16",
     "enable_flash_attention=True,remove_reshape=True", None),

    # INT8 tests with MMSE
    ("B1: INT8 normal (original)", ONNX_PATH, "int8",
     "quantized_dtype=w8a8,quantized_method=channel,quantized_algorithm=normal", DATASET),
    ("B2: INT8 mmse (original)", ONNX_PATH, "int8",
     "quantized_dtype=w8a8,quantized_method=channel,quantized_algorithm=mmse", DATASET),
    ("B3: INT8 normal + flash + rm_reshape (original)", ONNX_PATH, "int8",
     "quantized_dtype=w8a8,quantized_method=channel,quantized_algorithm=normal,enable_flash_attention=True,remove_reshape=True", DATASET),
    ("B4: INT8 mmse + flash + rm_reshape (surgery)", surg_path, "int8",
     "quantized_dtype=w8a8,quantized_method=channel,quantized_algorithm=mmse,enable_flash_attention=True,remove_reshape=True", DATASET),
  ]

  for name, onnx_path, dtype, extra, dataset in tests:
    if not os.path.exists(onnx_path):
      print(f"\n  SKIP {name}: ONNX not found")
      all_results.append({"name": name, "error": "onnx not found"})
      continue

    print(f"\n  Testing: {name}")
    r = run_sim_benchmark(name, onnx_path, dtype, extra, dataset)
    if 'mean_ms' in r:
      hz = 1000 / r['mean_ms']
      print(f"  ✅ {r['mean_ms']}ms ({hz:.1f} Hz) valid={r['valid']}")
    else:
      print(f"  ❌ FAILED: {r.get('error','?')[:60]}")
    all_results.append(r)

  # Summary
  print(f"\n\n{'='*90}")
  print(f"ADVANCED RESULTS SUMMARY")
  print(f"{'='*90}")
  successful = sorted([r for r in all_results if 'mean_ms' in r], key=lambda x: x['mean_ms'])
  print(f"{'Config':<52} {'ms':>6} {'Hz':>6} {'Valid':>6}")
  print("-" * 75)
  for r in successful:
    hz = 1000 / r['mean_ms']
    print(f"{r['name']:<52} {r['mean_ms']:>5.1f} {hz:>5.1f} {str(r['valid']):>6}")
  failed = [r for r in all_results if 'error' in r]
  if failed:
    print(f"\nFailed:")
    for r in failed:
      print(f"  {r['name']}: {r.get('error','?')[:60]}")

  # Save
  with open(RESULTS_FILE, 'w') as f:
    f.write("# 0.11 Supercombo Advanced Config Results\n")
    f.write(f"# Date: 2026-08-07\n\n")
    f.write(f"## Graph surgery\n")
    f.write(f"Original fallback ops: {fallback}\n")
    if changed1:
      f.write(f"After surgery fallback ops: {surg_fallback}\n\n")
    f.write(f"## Results\n\n")
    f.write(f"| Config | ms | Hz | Valid |\n")
    f.write(f"|--------|----|----|-------|\n")
    for r in successful:
      hz = round(1000/r['mean_ms'], 1)
      f.write(f"| {r['name']} | {r['mean_ms']} | {hz} | {r['valid']} |\n")
    for r in failed:
      f.write(f"| {r['name']} | FAIL | - | - |\n")
  print(f"\nResults saved to {RESULTS_FILE}")

if __name__ == "__main__":
  main()
