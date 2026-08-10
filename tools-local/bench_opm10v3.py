#!/usr/bin/env python3
"""Benchmark OPM10V3 RKNN models via simulator (load_onnx → build → init_runtime(None) → inference).

The simulator runs on CPU in Docker — timing is NOT the NPU production number,
but IS useful for relative comparison. Calibration ratios from previous work:
  Vision-heavy models: sim ~2.37x slower than device
  Policy-heavy models:  sim ~1.35x slower than device

Reference simulator results (from SIMULATOR-RESULTS.md):
  0.10 vision FP16:  66.9ms sim → 29.7ms device (2.25x)
  0.10 policy FP16:   3.7ms sim →  3.1ms device (1.19x)
  0.11 supercombo:   79.6ms sim → ~79.5ms device (1.0x — NPU-bound)

Run in Docker:
  docker run --rm --platform linux/arm64 -v "$PWD":/work -w /work \
    ubuntu:22.04 bash -c '
      apt-get update -qq && apt-get install -y -qq python3 python3-pip python3-dev libgl1 libglib2.0-0 cmake build-essential >/dev/null 2>&1
      pip3 install --no-input "https://github.com/airockchip/rknn-toolkit2/raw/master/rknn-toolkit2/packages/arm64/rknn_toolkit2-2.3.2-cp310-cp310-manylinux_2_17_aarch64.manylinux2014_aarch64.whl"
      pip3 install --no-input numpy
      python3 tools-local/bench_opm10v3.py
    '
"""
import numpy as np
import pickle
import time
from pathlib import Path

from rknn.api import RKNN

MODELS_DIR = Path(__file__).resolve().parents[1] / "selfdrive" / "modeld" / "models_dev" / "opm10v3"

models = [
    ("vision", "driving_vision_opset19.onnx", "driving_vision_opm10v3_metadata.pkl"),
    ("on_policy", "driving_on_policy_opset19.onnx", "driving_on_policy_opm10v3_metadata.pkl"),
    ("off_policy", "driving_off_policy_opset19.onnx", "driving_off_policy_opm10v3_metadata.pkl"),
]

total_sim_ms = 0
for name, onnx_file, meta_file in models:
    onnx_path = MODELS_DIR / onnx_file
    meta_path = MODELS_DIR / meta_file
    print(f"\n{'='*60}")
    print(f"  {name}")
    print(f"{'='*60}")

    if not onnx_path.exists():
        print(f"  SKIP: {onnx_path} not found")
        continue

    with open(meta_path, "rb") as f:
        meta = pickle.load(f)
    input_shapes = meta["input_shapes"]
    output_shape = meta["output_shapes"]["outputs"]
    print(f"  ONNX: {onnx_path.name} ({onnx_path.stat().st_size/1e6:.1f} MB)")
    print(f"  output: {output_shape}")

    # Dummy inputs
    dummy_inputs = []
    for inp_name, shape in input_shapes.items():
        if "img" in inp_name:
            arr = np.random.randint(0, 256, shape, dtype=np.uint8).astype(np.float16)
        else:
            arr = np.random.randn(*shape).astype(np.float16)
        dummy_inputs.append(np.ascontiguousarray(arr))

    rknn = RKNN(verbose=False)
    rknn.config(
        mean_values=[[]], std_values=[[]],
        target_platform="rk3588",
        quantized_dtype="w16a16i",
    )
    ret = rknn.load_onnx(model=str(onnx_path))
    if ret != 0:
        print(f"  FAIL load_onnx: {ret}")
        continue

    ret = rknn.build(do_quantization=False)
    if ret != 0:
        print(f"  FAIL build: {ret}")
        continue

    # Simulator: init_runtime(target=None)
    try:
        ret = rknn.init_runtime(target=None)
        if ret != 0:
            print(f"  FAIL init_runtime: {ret}")
            continue
    except Exception as e:
        print(f"  init_runtime error: {e}")
        continue

    print(f"  simulator initialized, running inference...")

    # Warmup
    for _ in range(3):
        rknn.inference(inputs=dummy_inputs, data_format="nchw")

    # Benchmark 10 runs
    times = []
    for _ in range(10):
        t0 = time.perf_counter()
        outputs = rknn.inference(inputs=dummy_inputs, data_format="nchw")
        t1 = time.perf_counter()
        times.append((t1 - t0) * 1000)

    avg_ms = np.mean(times)
    total_sim_ms += avg_ms
    out = outputs[0]
    expected = int(np.prod(output_shape))
    ok = "OK" if out.size == expected else "MISMATCH"
    print(f"  [{ok}] {out.size}/{expected} values, range=[{out.min():.3f}, {out.max():.3f}]")
    print(f"  sim avg: {avg_ms:.1f}ms  min: {np.min(times):.1f}ms  max: {np.max(times):.1f}ms")

    # Device estimate using calibration ratio
    if "img" in str(input_shapes):
        ratio = 2.37  # vision-heavy
    else:
        ratio = 1.35  # policy-heavy
    est_device = avg_ms / ratio
    print(f"  est device: ~{est_device:.1f}ms (sim/{ratio:.2f})")

    rknn.release()

sep = "=" * 60
print(f"\n{sep}")
print(f"RESULTS SUMMARY")
print(f"{sep}")
print(f"  Total sim time (3 models): {total_sim_ms:.1f}ms")

# Estimate device total: vision uses 2.37x, policies use 1.35x
# We need per-model estimates
print(f"\n  Reference (from SIMULATOR-RESULTS.md):")
print(f"    0.10 vision:  66.9ms sim → 29.7ms device = 30.5 Hz total (with 3.1ms policy)")
print(f"    0.11 supercombo: 79.6ms sim → 79.5ms device = 12.6 Hz")
print(f"\n  If OPM10V3 total sim ~{total_sim_ms:.0f}ms, apply calibration:")
print(f"    Vision-heavy portion (~{total_sim_ms*0.7:.0f}ms) / 2.37 = ~{total_sim_ms*0.7/2.37:.0f}ms device")
print(f"    Policy portion (~{total_sim_ms*0.3:.0f}ms) / 1.35 = ~{total_sim_ms*0.3/1.35:.0f}ms device")
total_est = total_sim_ms*0.7/2.37 + total_sim_ms*0.3/1.35
print(f"    Estimated device total: ~{total_est:.0f}ms = ~{1000/total_est:.1f} Hz")
print(f"\n  Real benchmark on KA2: python3 tools-local/bench_opm10v3.py")
print(f"{sep}")
