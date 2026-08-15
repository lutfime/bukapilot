#!/usr/bin/env python3
"""On-device: erf vs S=8-scaled opm10v3 policy heads on REAL (in-distribution) features.

Features come from the production vision model run on noisy images — the same
construction ModelState3SplitRKNN uses (vision_output[117:629], 25-timestep stack).
Synthetic random features explode the net to inf in ALL variants (see
test_scaled_vs_erf.py); only these features match the training distribution.

Reports per head/variant: inf%, all-zero%, max|out|, erf-vs-scaled diff on
both-finite frames.
"""
import sys
import numpy as np

sys.path.insert(0, "/data/openpilot")
from selfdrive.modeld.runners.driving_3split_rknn import Driving3SplitRKNNRunner  # noqa: E402
from rknnlite.api import RKNNLite  # noqa: E402

MD = "/data/openpilot/selfdrive/modeld/models/"
HIDDEN_SLICE = slice(117, 629)
CONTEXT = 25
IMG_SHAPE = (1, 12, 128, 256)
WINDOWS = 60  # feature windows to test

desire = np.zeros((1, 25, 8), dtype=np.float16)
tc = np.array([[1.0, 0.0]], dtype=np.float16)


def policy(model, fb):
  r = RKNNLite(verbose=False)
  assert r.load_rknn(MD + model) == 0
  assert r.init_runtime() == 0
  inf = zero = 0
  outs = []
  for f in fb:
    o = r.inference(inputs=[desire, tc, f], data_type="float16")[0].astype(np.float32)
    if not np.isfinite(o).all():
      inf += 1
    elif np.abs(o).max() < 1e-6:
      zero += 1
    else:
      outs.append(o)
  r.release()
  return inf, zero, outs


runner = Driving3SplitRKNNRunner(MD)
rng = np.random.RandomState(12345)
hidden, fbs = [], []
for i in range(CONTEXT + WINDOWS):
  img = np.ascontiguousarray(rng.randint(0, 256, IMG_SHAPE, dtype=np.uint8))
  v = np.asarray(runner.run_vision(img, img), dtype=np.float32).reshape(-1)
  hidden.append(v[HIDDEN_SLICE].copy())
  if len(hidden) >= CONTEXT:
    fbs.append(np.ascontiguousarray(
      np.stack(hidden[-CONTEXT:]).reshape(1, 25, 512).astype(np.float16)))
hs = np.stack(hidden)
print(f"vision hidden_state: range [{hs.min():.3f}, {hs.max():.3f}]  std {hs.std():.3f}  ({len(fbs)} feature windows)")

for head, erf_file, scaled_file in [
    ("on_policy", "driving_on_policy_opm10v3_erf.rknn", "driving_on_policy_opm10v3_scaled.rknn"),
    ("off_policy", "driving_off_policy_opm10v3.rknn", "driving_off_policy_opm10v3_scaled.rknn")]:
  print(f"\n===== {head} (real features) =====")
  res = {}
  for tag, f in [("erf", erf_file), ("scaled", scaled_file)]:
    inf, zero, outs = policy(f, fbs)
    res[tag] = outs
    mx = max((float(np.abs(o).max()) for o in outs), default=float("nan"))
    print(f"  {tag:6s} inf={100*inf/WINDOWS:5.1f}%  zero={100*zero/WINDOWS:5.1f}%  max|out|={mx:.4g}  finite_frames={len(outs)}")
  n = min(len(res["erf"]), len(res["scaled"]))
  if n:
    d = np.array([np.abs(a - b).max() for a, b in zip(res["erf"][:n], res["scaled"][:n])])
    print(f"  both-finite={n}/{WINDOWS}  |erf-scaled| median={np.median(d):.4g} max={d.max():.4g}")
