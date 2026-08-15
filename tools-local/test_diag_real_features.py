#!/usr/bin/env python3
"""On-device: DIAG models + REAL features — which layers go inf in-distribution.

Same as test_diag_overflow.py but feeds features built from real vision
hidden_states (production construction) instead of synthetic random walks.
Reports the first inf layer index and the full inf-layer list, per head.
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
WINDOWS = 40

desire = np.zeros((1, 25, 8), dtype=np.float16)
tc = np.array([[1.0, 0.0]], dtype=np.float16)

runner = Driving3SplitRKNNRunner(MD)
rng = np.random.RandomState(999)
hidden, fbs = [], []
for _ in range(CONTEXT + WINDOWS):
  img = np.ascontiguousarray(rng.randint(0, 256, IMG_SHAPE, dtype=np.uint8))
  v = np.asarray(runner.run_vision(img, img), dtype=np.float32).reshape(-1)
  hidden.append(v[HIDDEN_SLICE].copy())
  if len(hidden) >= CONTEXT:
    fbs.append(np.ascontiguousarray(np.stack(hidden[-CONTEXT:]).reshape(1, 25, 512).astype(np.float16)))
print(f"built {len(fbs)} real-feature windows")

for head, f in [("on_policy", "driving_on_policy_opm10v3_diag.rknn"),
                ("off_policy", "driving_off_policy_opm10v3_diag.rknn")]:
  r = RKNNLite(verbose=False)
  assert r.load_rknn(MD + f) == 0
  assert r.init_runtime() == 0
  inf_counts = {}
  for fb in fbs:
    outs = r.inference(inputs=[desire, tc, fb], data_type="float16")
    for i, o in enumerate(outs):
      if o is not None and (np.isinf(o).any() or np.isnan(o).any()):
        inf_counts[i] = inf_counts.get(i, 0) + 1
  r.release()
  n_out = len(outs)
  bad = sorted(inf_counts)
  print(f"\n===== {head} diag (real features): {len(bad)}/{n_out} outputs ever-inf =====")
  if bad:
    rates = sorted(((100 * c / WINDOWS, i) for i, c in inf_counts.items()), reverse=True)
    print(f"  worst layer: output[{rates[0][1]}] {rates[0][0]:.0f}% of frames")
    print(f"  first-inf indices (graph order): {bad[:15]}")
  else:
    print("  NO inf in any output")
