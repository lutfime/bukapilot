#!/usr/bin/env python3
"""On-device: Gelu-variant opm10v3 policy heads vs erf baseline on REAL features.

Variants (commit 17dc626, untested): tanh (GPT-2 approx, different NPU kernel)
and erfsplit (erf sum-form, different structure). Features are in-distribution
(vision hidden_states, production construction) — the only regime that
discriminates variants (synthetic = everything inf; see findings doc).

PASS for a variant: inf% << erf's, and outputs track erf on both-finite frames
(each variant approximates exact Gelu to <0.03 per activation, so a healthy
variant must stay near the erf baseline; big divergence = miscompile, like S=8).
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
WINDOWS = 60

desire = np.zeros((1, 25, 8), dtype=np.float16)
tc = np.array([[1.0, 0.0]], dtype=np.float16)

runner = Driving3SplitRKNNRunner(MD)
rng = np.random.RandomState(12345)  # same seed as the scaled test -> same features
hidden, fbs = [], []
for _ in range(CONTEXT + WINDOWS):
  img = np.ascontiguousarray(rng.randint(0, 256, IMG_SHAPE, dtype=np.uint8))
  v = np.asarray(runner.run_vision(img, img), dtype=np.float32).reshape(-1)
  hidden.append(v[HIDDEN_SLICE].copy())
  if len(hidden) >= CONTEXT:
    fbs.append(np.ascontiguousarray(np.stack(hidden[-CONTEXT:]).reshape(1, 25, 512).astype(np.float16)))
print(f"built {len(fbs)} real-feature windows (hidden range [{np.stack(hidden).min():.2f}, {np.stack(hidden).max():.2f}])")

for head, variants in [
    ("on_policy", [("erf", "driving_on_policy_opm10v3_erf.rknn"),
                   ("tanh", "driving_on_policy_opm10v3_tanh.rknn"),
                   ("erfsplit", "driving_on_policy_opm10v3_erfsplit.rknn")]),
    ("off_policy", [("erf", "driving_off_policy_opm10v3.rknn"),
                    ("tanh", "driving_off_policy_opm10v3_tanh.rknn"),
                    ("erfsplit", "driving_off_policy_opm10v3_erfsplit.rknn")])]:
  print(f"\n===== {head} (real features) =====")
  outs_by_tag = {}
  for tag, f in variants:
    r = RKNNLite(verbose=False)
    assert r.load_rknn(MD + f) == 0, f
    assert r.init_runtime() == 0
    inf = zero = 0
    outs = []
    for fb in fbs:
      o = r.inference(inputs=[desire, tc, fb], data_type="float16")[0].astype(np.float32)
      if not np.isfinite(o).all():
        inf += 1
      elif np.abs(o).max() < 1e-6:
        zero += 1
      else:
        outs.append(o)
    r.release()
    outs_by_tag[tag] = outs
    mx = max((float(np.abs(o).max()) for o in outs), default=float("nan"))
    print(f"  {tag:8s} inf={100*inf/WINDOWS:5.1f}%  zero={100*zero/WINDOWS:5.1f}%  max|out|={mx:.4g}  finite={len(outs)}")
  base = outs_by_tag["erf"]
  for tag in ["tanh", "erfsplit"]:
    n = min(len(base), len(outs_by_tag[tag]))
    if n:
      d = np.array([np.abs(a - b).max() for a, b in zip(base[:n], outs_by_tag[tag][:n])])
      print(f"  erf-vs-{tag}: both-finite={n}  |diff| median={np.median(d):.4g} max={d.max():.4g}")
