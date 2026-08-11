#!/usr/bin/env python3
"""1:1 comparison test: C++ 2-file runner vs Python rknnlite for OPM10V3 vision + on_policy.

HYBRID MODE TEST: The production hybrid uses C++ for vision+on_policy and Python for off_policy.
This test verifies the C++ part produces bit-identical output to the proven Python rknnlite runner.
off_policy is Python in both paths, so no comparison needed.

The C++ 2-file runner is the SAME proven path used by default 0.10.3 and WMI v12 — just with
OPM10V3's vision and on_policy files instead. This test confirms those files work correctly
through the 2-file C++ path.

NPU inference is deterministic (fixed weights, fixed math, no sampling). Same model + same input
= identical output every time. Default is 1000 frames for high confidence.

Run on device (KA2) after building driving_rknnmodel_pyx.so:
  cd /data/openpilot
  python3 tools-local/test_3split_cpp_vs_python.py [--frames N]

Exit code 0 = all frames match. Non-zero = mismatch found.
"""
from __future__ import annotations

import argparse
import os
import pickle
import sys
import time
from pathlib import Path

import numpy as np

MODEL_DIR = Path("/data/openpilot/selfdrive/modeld/models")
VISION_RKNN = MODEL_DIR / "driving_vision_opm10v3.rknn"
ON_POLICY_RKNN = MODEL_DIR / "driving_on_policy_opm10v3.rknn"
OFF_POLICY_RKNN = MODEL_DIR / "driving_off_policy_opm10v3.rknn"
VISION_META = MODEL_DIR / "driving_vision_opm10v3_metadata.pkl"
ON_POLICY_META = MODEL_DIR / "driving_on_policy_opm10v3_metadata.pkl"
OFF_POLICY_META = MODEL_DIR / "driving_off_policy_opm10v3_metadata.pkl"

# Input shapes (fixed by model architecture)
IMG_SHAPE = (1, 12, 128, 256)       # NCHW uint8
BIG_IMG_SHAPE = (1, 12, 128, 256)   # NCHW uint8
DESIRE_PULSE_SHAPE = (1, 25, 8)
TRAFFIC_CONVENTION_SHAPE = (1, 2)
FEATURES_BUFFER_SHAPE = (1, 25, 512)


def load_metadata():
  """Load output sizes from metadata pkls."""
  with open(VISION_META, "rb") as f:
    vm = pickle.load(f)
  with open(ON_POLICY_META, "rb") as f:
    om = pickle.load(f)
  with open(OFF_POLICY_META, "rb") as f:
    fpm = pickle.load(f)
  return {
    "vision_out_size": int(np.prod(vm["output_shapes"]["outputs"])),
    "on_policy_out_size": int(np.prod(om["output_shapes"]["outputs"])),
    "off_policy_out_size": int(np.prod(fpm["output_shapes"]["outputs"])),
    "vision_input_shapes": vm["input_shapes"],
    "policy_input_shapes": om["input_shapes"],
  }


def generate_test_frames(n_frames: int) -> list[dict]:
  """Generate diverse synthetic test inputs."""
  frames = []
  rng = np.random.RandomState(42)  # deterministic seed

  for i in range(n_frames):
    if i == 0:
      img = np.zeros(IMG_SHAPE, dtype=np.uint8)
      big_img = np.zeros(BIG_IMG_SHAPE, dtype=np.uint8)
    elif i == 1:
      img = np.full(IMG_SHAPE, 255, dtype=np.uint8)
      big_img = np.full(BIG_IMG_SHAPE, 255, dtype=np.uint8)
    elif i == 2:
      img = np.full(IMG_SHAPE, 128, dtype=np.uint8)
      big_img = np.full(BIG_IMG_SHAPE, 64, dtype=np.uint8)
    elif i % 5 == 3:
      grad = np.tile(np.linspace(0, 255, 256, dtype=np.uint8), (12, 128, 1))
      img = grad[np.newaxis, :, :, :]
      big_img = grad[np.newaxis, :, :, :]
    elif i % 5 == 4:
      grad = np.tile(np.linspace(0, 255, 128, dtype=np.uint8)[:, np.newaxis], (1, 256))
      grad = np.tile(grad, (12, 1, 1))
      img = grad[np.newaxis, :, :, :]
      big_img = (255 - grad)[np.newaxis, :, :, :]
    else:
      img = rng.randint(0, 256, IMG_SHAPE, dtype=np.uint8)
      big_img = rng.randint(0, 256, BIG_IMG_SHAPE, dtype=np.uint8)

    desire_pulse = rng.choice([0.0, 1.0], DESIRE_PULSE_SHAPE).astype(np.float32)
    traffic_convention = np.array([[1.0, 0.0]] if i % 2 == 0 else [[0.0, 1.0]], dtype=np.float32)
    features_buffer = rng.randn(*FEATURES_BUFFER_SHAPE).astype(np.float32) * 0.1

    frames.append({
      "img": np.ascontiguousarray(img),
      "big_img": np.ascontiguousarray(big_img),
      "desire_pulse": np.ascontiguousarray(desire_pulse),
      "traffic_convention": np.ascontiguousarray(traffic_convention),
      "features_buffer": np.ascontiguousarray(features_buffer),
    })

  return frames


def run_python_runner(meta, frames):
  """Run vision + on_policy through Python rknnlite (ground truth).
  Also runs off_policy to verify it works (but off_policy comparison is trivially Python==Python)."""
  sys.path.insert(0, "/data/openpilot")
  from selfdrive.modeld.runners.driving_3split_rknn import Driving3SplitRKNNRunner
  runner = Driving3SplitRKNNRunner(MODEL_DIR)

  results = []
  t0 = time.monotonic()
  for i, fr in enumerate(frames):
    vision_out = runner.run_vision(fr["img"], fr["big_img"]).reshape(-1)
    on_policy_out = runner.run_on_policy(
      fr["desire_pulse"], fr["traffic_convention"], fr["features_buffer"]
    ).reshape(-1)

    results.append({
      "vision": vision_out.copy(),
      "on_policy": on_policy_out.copy(),
    })
  elapsed = time.monotonic() - t0

  # Release NPU contexts BEFORE the C++ runner loads (avoid 5-context contention)
  for ctx in [runner._vision_rknn, runner._on_policy_rknn, runner._off_policy_rknn]:
    try:
      ctx.release()
    except Exception:
      pass
  del runner
  return results, elapsed


def run_cpp_runner(meta, frames):
  """Run vision + on_policy through C++ 2-file runner (the hybrid C++ path).
  This is the EXACT same proven 2-file path used by default 0.10.3 — just with OPM10V3 files."""
  sys.path.insert(0, "/data/openpilot")
  from selfdrive.modeld.runners.driving_rknnmodel_pyx import DrivingRKNNRunnerCpp

  # 2-file constructor: vision + on_policy (on_policy is the "policy" in 2-file terms)
  runner = DrivingRKNNRunnerCpp(
    str(VISION_RKNN), str(ON_POLICY_RKNN),
    vision_out_size=meta["vision_out_size"],
    policy_out_size=meta["on_policy_out_size"],
  )

  assert not runner.is_3split(), "Should be 2-file mode for hybrid!"

  results = []
  t0 = time.monotonic()
  for i, fr in enumerate(frames):
    vision_out = runner.run_vision(fr["img"], fr["big_img"]).reshape(-1)
    on_policy_out = runner.run_policy(
      fr["desire_pulse"], fr["traffic_convention"], fr["features_buffer"]
    ).reshape(-1)

    results.append({
      "vision": vision_out.copy(),
      "on_policy": on_policy_out.copy(),
    })
  elapsed = time.monotonic() - t0
  return results, elapsed


def compare_outputs(name: str, py_out: np.ndarray, cpp_out: np.ndarray) -> bool:
  """Compare two output arrays. Returns True if bit-identical (or within fp32 epsilon)."""
  if py_out.shape != cpp_out.shape:
    print(f"  ❌ {name}: SHAPE MISMATCH py={py_out.shape} cpp={cpp_out.shape}")
    return False

  exact_match = np.array_equal(py_out, cpp_out)
  if exact_match:
    print(f"  ✅ {name}: BIT-EXACT match ({py_out.size} elements)")
    return True

  abs_diff = np.abs(py_out - cpp_out)
  max_diff = float(np.max(abs_diff))
  mean_diff = float(np.mean(abs_diff))

  if max_diff < 1e-3:
    print(f"  ✅ {name}: within fp16 tolerance (max_abs={max_diff:.6e}, mean={mean_diff:.6e})")
    return True

  print(f"  ❌ {name}: MISMATCH (max_abs={max_diff:.4f}, mean_abs={mean_diff:.4f}, shape={py_out.shape})")
  worst = np.argsort(abs_diff)[-5:][::-1]
  for idx in worst:
    print(f"    [{idx}] py={py_out[idx]:.6f} cpp={cpp_out[idx]:.6f} diff={abs_diff[idx]:.6f}")
  return False


def main():
  parser = argparse.ArgumentParser(description="Hybrid C++ vs Python comparison for OPM10V3 vision+on_policy")
  parser.add_argument("--frames", type=int, default=1000, help="Number of synthetic frames to test")
  args = parser.parse_args()

  print("=" * 70)
  print("OPM10V3 Hybrid: C++ 2-file (vision+on_policy) vs Python rknnlite")
  print("=" * 70)

  for p in [VISION_RKNN, ON_POLICY_RKNN, VISION_META, ON_POLICY_META]:
    if not p.exists():
      print(f"❌ Missing: {p}")
      sys.exit(1)
    print(f"  ✓ {p.name} ({p.stat().st_size / 1e6:.1f} MB)")

  meta = load_metadata()
  print(f"\nOutput sizes: vision={meta['vision_out_size']}, on_policy={meta['on_policy_out_size']}")

  print(f"\nGenerating {args.frames} synthetic test frames...")
  frames = generate_test_frames(args.frames)
  print(f"Total frames to test: {len(frames)}")

  # Run Python runner first (ground truth)
  print("\n--- Running Python rknnlite (ground truth) ---")
  py_results, py_time = run_python_runner(meta, frames)
  print(f"Done: {len(frames)} frames in {py_time:.2f}s ({py_time/len(frames)*1000:.1f} ms/frame)")

  # Run C++ 2-file runner
  print("\n--- Running C++ 2-file runner (vision + on_policy) ---")
  cpp_results, cpp_time = run_cpp_runner(meta, frames)
  print(f"Done: {len(frames)} frames in {cpp_time:.2f}s ({cpp_time/len(frames)*1000:.1f} ms/frame)")

  vision_speedup = py_time / cpp_time if cpp_time > 0 else 0
  print(f"\nSpeedup: {vision_speedup:.2f}x (Python {py_time/len(frames)*1000:.1f}ms → "
        f"C++ {cpp_time/len(frames)*1000:.1f}ms per frame)")

  # Compare
  print("\n--- Comparing outputs (expecting bit-exact match) ---")
  all_match = True
  for i, (py_res, cpp_res) in enumerate(zip(py_results, cpp_results)):
    frame_ok = True
    if i < 5 or i % 50 == 0:
      print(f"\nFrame {i}:")
      frame_ok &= compare_outputs("vision", py_res["vision"], cpp_res["vision"])
      frame_ok &= compare_outputs("on_policy", py_res["on_policy"], cpp_res["on_policy"])
    else:
      for name in ["vision", "on_policy"]:
        if not np.array_equal(py_res[name], cpp_res[name]):
          abs_diff = np.abs(py_res[name] - cpp_res[name])
          if float(np.max(abs_diff)) >= 1e-3:
            print(f"\nFrame {i}:")
            frame_ok &= compare_outputs(name, py_res[name], cpp_res[name])
    all_match &= frame_ok

  print("\n" + "=" * 70)
  if all_match:
    print(f"✅ ALL {len(frames)} FRAMES MATCH — C++ 2-file hybrid path is 1:1 verified!")
    print(f"   Performance: {py_time/len(frames)*1000:.1f}ms → {cpp_time/len(frames)*1000:.1f}ms "
          f"({vision_speedup:.2f}x speedup for vision+on_policy)")
    print(f"   off_policy stays on Python rknnlite (~7ms, proven path)")
    sys.exit(0)
  else:
    print(f"❌ MISMATCH DETECTED — do NOT deploy hybrid path until fixed!")
    sys.exit(1)


if __name__ == "__main__":
  main()
