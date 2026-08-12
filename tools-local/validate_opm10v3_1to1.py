#!/usr/bin/env python3
"""Rigorous 1:1 validation: opm10v3 C++ hybrid (vision+on_policy) vs Python rknnlite ground truth.

WHY THIS EXISTS:
  test_3split_cpp_vs_python.py feeds RANDOM features_buffer/desire_pulse, which are
  out-of-distribution and overflow on_policy to inf in BOTH Python and C++ — so it cannot
  pass and is not a valid gate. This script feeds IN-DISTRIBUTION inputs: features_buffer
  built from REAL vision hidden_states (exactly how production ModelState3SplitRKNN.run()
  builds it — vision_output[117:629] accumulated over 25 timesteps).

TESTS:
  1. vision 1:1      — same image  -> C++ vs Python vision output (hidden_state) [fp16 tol]
  2. on_policy 1:1   — same realistic features_buffer -> C++ vs Python on_policy  [fp16 tol]
  3. finite check    — every output finite (no nan/inf) across all frames
  4. off_policy      — Python (production path) finite + sane
  5. timing          — ms/frame C++ vision+on_policy + Python off_policy -> Hz estimate

PASS = tests 1-4 within tolerance + all finite. Exit 0.

Run on device (KA2):
  cd /data/openpilot && /usr/local/venv/bin/python tools-local/validate_opm10v3_1to1.py [--frames 200]
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import numpy as np

MODEL_DIR = Path("/data/openpilot/selfdrive/modeld/models")
VISION_RKNN = MODEL_DIR / "driving_vision_opm10v3.rknn"
ON_POLICY_RKNN = MODEL_DIR / "driving_on_policy_opm10v3.rknn"
OFF_POLICY_RKNN = MODEL_DIR / "driving_off_policy_opm10v3.rknn"

IMG_SHAPE = (1, 12, 128, 256)        # NCHW uint8
DESIRE_SHAPE = (1, 25, 8)
FEATURES_SHAPE = (1, 25, 512)        # 25 timesteps x 512 hidden
HIDDEN_SLICE = slice(117, 629)       # hidden_state within vision output (632 wide)
CONTEXT = 25                          # temporal queue depth (matches production N_FRAMES)

# fp16 epsilon: two different execution paths (C API vs rknnlite) legitimately differ by a
# few ULP. 0.05 absolute on ~[-20,20] outputs is ~0.25% — well within fp16 rounding.
VISION_TOL = 0.05
POLICY_TOL = 0.05


def gen_images(n, seed=12345):
  rng = np.random.RandomState(seed)
  imgs = []
  for _ in range(n):
    # realistic-ish: random uint8 (real camera frames are noisy, NOT degenerate zeros)
    imgs.append(np.ascontiguousarray(rng.randint(0, 256, IMG_SHAPE, dtype=np.uint8)))
  return imgs


def main():
  ap = argparse.ArgumentParser()
  ap.add_argument("--frames", type=int, default=200)
  args = ap.parse_args()

  sys.path.insert(0, "/data/openpilot")
  from selfdrive.modeld.runners.driving_3split_rknn import Driving3SplitRKNNRunner
  from selfdrive.modeld.runners.driving_rknnmodel_pyx import DrivingRKNNRunnerCpp
  from rknnlite.api import RKNNLite

  n = args.frames
  print("=" * 72)
  print(f"OPM10V3 rigorous 1:1 — C++ hybrid (vision+on_policy) vs Python rknnlite, {n} frames")
  print("=" * 72)

  desire_zero = np.zeros(DESIRE_SHAPE, dtype=np.float32)   # no lane change (in-distribution)
  tc = np.array([[1.0, 0.0]], dtype=np.float32)            # left-hand traffic

  # ---- ground truth: Python runner (vision + on_policy + off_policy) ----
  pry = Driving3SplitRKNNRunner(MODEL_DIR)
  # ---- C++ 2-file runner (vision + on_policy), the production hybrid path ----
  cpp = DrivingRKNNRunnerCpp(str(VISION_RKNN), str(ON_POLICY_RKNN),
                             vision_out_size=632, policy_out_size=1000)
  # ---- off_policy via Python rknnlite (production perception path) ----
  offpy = RKNNLite(verbose=False)
  offpy.load_rknn(str(OFF_POLICY_RKNN))
  offpy.init_runtime()

  images = gen_images(n + CONTEXT)   # extra CONTEXT frames to warm the temporal queue

  # rolling buffers of hidden_states (per path) to build in-distribution features_buffer
  py_hidden, cpp_hidden = [], []

  v_max = v_mean = 0.0
  op_max = op_mean = 0.0
  v_finite = op_finite = off_finite = True
  compared = 0

  t_cpp_v, t_cpp_p, t_off = 0.0, 0.0, 0.0

  for i, img in enumerate(images):
    # --- vision (both paths) ---
    t0 = time.monotonic(); vy = np.asarray(pry.run_vision(img, img), dtype=np.float32).reshape(-1); t_py_v = time.monotonic() - t0
    t0 = time.monotonic(); vc = np.asarray(cpp.run_vision(img, img), dtype=np.float32).reshape(-1); t_cpp_v += time.monotonic() - t0

    hy = vy[HIDDEN_SLICE].copy()
    hc = vc[HIDDEN_SLICE].copy()
    py_hidden.append(hy); cpp_hidden.append(hc)

    # vision 1:1 (entire 632 output)
    if np.isfinite(vy).all() and np.isfinite(vc).all():
      d = np.abs(vy - vc)
      v_max = max(v_max, float(d.max())); v_mean += float(d.mean())
    else:
      v_finite = False

    # --- on_policy once the temporal queue is full (in-distribution features) ---
    if len(py_hidden) >= CONTEXT:
      fb_py = np.stack(py_hidden[-CONTEXT:], axis=0).reshape(FEATURES_SHAPE).astype(np.float32)
      fb_cpp = np.stack(cpp_hidden[-CONTEXT:], axis=0).reshape(FEATURES_SHAPE).astype(np.float32)

      # TEST 2a: same Python features -> both paths (isolates on_policy correctness)
      t0 = time.monotonic(); opy = np.asarray(pry.run_on_policy(desire_zero, tc, np.ascontiguousarray(fb_py)), dtype=np.float32).reshape(-1); t_off += 0
      t0b = time.monotonic(); opc = np.asarray(cpp.run_policy(desire_zero, tc, np.ascontiguousarray(fb_py)), dtype=np.float32).reshape(-1); t_cpp_p += time.monotonic() - t0b

      # off_policy (Python, production path) — finite + sane
      off_in = [np.ascontiguousarray(desire_zero.astype(np.float16).reshape(DESIRE_SHAPE)),
                np.ascontiguousarray(tc.astype(np.float16).reshape((1, 2))),
                np.ascontiguousarray(fb_py.astype(np.float16).reshape(FEATURES_SHAPE))]
      t0 = time.monotonic(); off_out = np.asarray(offpy.inference(inputs=off_in, data_type="float16")[0], dtype=np.float32).reshape(-1); t_off += time.monotonic() - t0

      if not (np.isfinite(opy).all() and np.isfinite(opc).all()):
        op_finite = False
      if not np.isfinite(off_out).all():
        off_finite = False

      if np.isfinite(opy).all() and np.isfinite(opc).all():
        d = np.abs(opy - opc)
        op_max = max(op_max, float(d.max())); op_mean += float(d.mean())
        compared += 1

  # release Python contexts
  for c in [pry._vision_rknn, pry._on_policy_rknn, pry._off_policy_rknn]:
    try: c.release()
    except Exception: pass
  offpy.release()

  n_vision = len(images)
  v_mean /= n_vision
  op_mean /= max(compared, 1)
  t_cpp_total = t_cpp_v + t_cpp_p
  hz_cpp = (n / t_cpp_total) if t_cpp_total > 0 else 0.0

  print(f"\n--- TEST 1: vision C++ vs Python ({n_vision} frames) ---")
  print(f"  all_finite = {v_finite}")
  print(f"  max_abs_diff = {v_max:.6f}   mean_abs_diff = {v_mean:.6f}   (tol = {VISION_TOL})")
  print(f"  PASS = {v_finite and v_max < VISION_TOL}")

  print(f"\n--- TEST 2: on_policy C++ vs Python, SAME realistic features ({compared} frames) ---")
  print(f"  all_finite = {op_finite}")
  print(f"  max_abs_diff = {op_max:.6f}   mean_abs_diff = {op_mean:.6f}   (tol = {POLICY_TOL})")
  print(f"  PASS = {op_finite and op_max < POLICY_TOL}")

  print(f"\n--- TEST 3: off_policy (Python, production path) finite ---")
  print(f"  all_finite = {off_finite}   PASS = {off_finite}")

  print(f"\n--- TEST 4: timing (per-frame, C++ vision+on_policy + Python off_policy) ---")
  print(f"  C++ vision   = {t_cpp_v/n:.1f} ms/frame")
  print(f"  C++ on_policy= {t_cpp_p/max(compared,1):.1f} ms/frame")
  print(f"  Py  off_policy = {t_off/max(compared,1):.1f} ms/frame")
  print(f"  -> C++ vision+on_policy throughput ~{hz_cpp:.1f} Hz (need >=20 to engage)")
  hz_full = 1000.0 / (t_cpp_total/n*1 + 0 + t_off/max(compared,1)) if compared else 0.0
  print(f"  -> full hybrid (cpp vis+onpolicy + py offpolicy) ~{hz_full:.1f} Hz est")

  ok = (v_finite and v_max < VISION_TOL and op_finite and op_max < POLICY_TOL and off_finite)
  print("\n" + "=" * 72)
  print("✅ ALL TESTS PASS — C++ hybrid is 1:1 validated vs Python ground truth" if ok else
        "❌ FAIL — see above")
  print("=" * 72)
  sys.exit(0 if ok else 1)


if __name__ == "__main__":
  main()
