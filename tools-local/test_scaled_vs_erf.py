#!/usr/bin/env python3
"""On-device: erf (S=1) vs weight-scaled (S=8) opm10v3 policy heads.

Two input regimes (synthetic — real-distribution features unavailable offroad):
  wild: std 0.3 random walk, clip +-1.5  (same as test_diag_overflow.py)
  tame: std 0.1 random walk, clip +-1.36 (closer to real hidden-state range)

Reports per model/regime: inf% of frames, all-zero% (the INT8 trap), output
magnitude, and erf-vs-scaled element diff on frames where both are finite.
"""
import numpy as np
from rknnlite.api import RKNNLite

MD = "/data/openpilot/selfdrive/modeld/models/"
FRAMES = 30


def features(seed, regime):
  rng = np.random.RandomState(seed)
  dp = np.zeros((1, 25, 8), dtype=np.float16)
  tc = np.array([[1.0, 0.0]], dtype=np.float16)
  start, step, clip = (0.3, 0.05, 1.5) if regime == "wild" else (0.1, 0.03, 1.36)
  base = (rng.randn(512) * start).astype(np.float16)
  fb = np.zeros((1, 25, 512), dtype=np.float16)
  for t in range(25):
    base = np.clip(base + (rng.randn(512) * step).astype(np.float16),
                   np.float16(-clip), np.float16(clip))
    fb[0, t] = base
  return dp, tc, fb


def run(model, regime):
  r = RKNNLite(verbose=False)
  assert r.load_rknn(MD + model) == 0
  assert r.init_runtime() == 0
  inf = zero = 0
  outs = []
  for i in range(FRAMES):
    o = r.inference(inputs=list(features(1000 + i, regime)), data_type="float16")[0].astype(np.float32)
    if not np.isfinite(o).all():
      inf += 1
    elif np.abs(o).max() < 1e-6:
      zero += 1
    else:
      outs.append(o)
  r.release()
  return {"inf%": 100 * inf / FRAMES, "zero%": 100 * zero / FRAMES,
          "max|out|": max((float(np.abs(o).max()) for o in outs), default=float("nan"))}, outs


for head in ["on_policy", "off_policy"]:
  print(f"\n===== {head} =====")
  results = {}
  for regime in ["wild", "tame"]:
    for tag, f in [("erf", f"driving_{head}_opm10v3_erf.rknn" if head == "on_policy" else f"driving_{head}_opm10v3.rknn"),
                   ("scaled", f"driving_{head}_opm10v3_scaled.rknn")]:
      stats, outs = run(f, regime)
      results[(regime, tag)] = outs
      print(f"  {regime:4s} {tag:6s} inf={stats['inf%']:5.1f}%  zero={stats['zero%']:5.1f}%  max|out|={stats['max|out|']:.4g}")
  for regime in ["wild", "tame"]:
    a, b = results[(regime, "erf")], results[(regime, "scaled")]
    n = min(len(a), len(b))
    if n:
      diffs = np.array([np.abs(x - y).max() for x, y in zip(a[:n], b[:n])])
      print(f"  {regime:4s} both-finite frames={n}/{FRAMES}  max|erf-scaled| median={np.median(diffs):.4g} max={diffs.max():.4g}")
    else:
      print(f"  {regime:4s} no both-finite frames to compare")
