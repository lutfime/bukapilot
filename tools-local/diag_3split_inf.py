#!/usr/bin/env python3
"""Diagnose the opm10v3 C++ 3-split inf bug. Run ON DEVICE:
    cd /data/openpilot && /usr/local/venv/bin python tools-local/diag_3split_inf.py

Prints the 3 isolation results that pinpoint the NPU multi-context corruption:
  A) on_policy with NO vision first  -> finite, correct (proves logic is right)
  B) on_policy AFTER vision (non-zero) -> output changes + fb input ignored (corruption)
  C) identical input 3x              -> deterministic but inf (not stateful; NPU-state dependent)

See tools-local/OPM10V3-CPP-3SPLIT-FINDINGS.md for full context.
Requires the new driving_rknnmodel_pyx.so (built with the 3-file C++ runner, commit cab02c8).
"""
import sys
import numpy as np
from pathlib import Path
from openpilot.selfdrive.modeld.runners.driving_rknnmodel_pyx import DrivingRKNNRunnerCpp

MD = Path("/data/openpilot/selfdrive/modeld/models")
r = DrivingRKNNRunnerCpp(
    str(MD / "driving_vision_opm10v3.rknn"),
    str(MD / "driving_on_policy_opm10v3.rknn"),
    str(MD / "driving_off_policy_opm10v3.rknn"),
    vision_out_size=632, policy_out_size=1000, off_policy_out_size=1940,
)

dp = np.zeros(200, dtype=np.float32); dp[0] = 1
tc = np.zeros(2, dtype=np.float32); tc[0] = 1
fb = np.full(12800, 0.05, dtype=np.float32)
fb_zero = np.zeros(12800, dtype=np.float32)

print("=" * 60)
print("A) on_policy with NO vision first (fb=0.05):")
pa = r.run_policy(dp, tc, fb).copy()
print(f"   inf={np.isinf(pa).any()}  range=[{pa.min():.2f}, {pa.max():.2f}]  (expect finite)")

print("B) run vision (non-zero=128), then on_policy again (fb=0.05):")
img = np.full((1, 12, 128, 256), 128, dtype=np.uint8)
r.run_vision(img, img)
pb = r.run_policy(dp, tc, fb).copy()
print(f"   inf={np.isinf(pb).any()}  range=[{pb.min():.2f}, {pb.max():.2f}]")
pc = r.run_policy(dp, tc, fb_zero).copy()
print(f"   then on_policy with fb=0:  inf={np.isinf(pc).any()}  range=[{pc.min():.2f}, {pc.max():.2f}]")
print(f"   B==C (fb ignored)? {np.array_equal(pb, pc)}  <- True = vision corrupted policy state")

print("C) identical input 3x (after vision):")
o1 = r.run_policy(dp, tc, fb).copy()
o2 = r.run_policy(dp, tc, fb).copy()
o3 = r.run_policy(dp, tc, fb).copy()
print(f"   o1==o2==o3: {np.array_equal(o1,o2) and np.array_equal(o2,o3)}  "
      f"(True = deterministic; not a stateful bug, the inf is NPU-state-dependent)")
print(f"   all inf? {np.isinf(o1).any() and np.isinf(o2).any()}")
print("=" * 60)
print("If A=finite, B==C=True, C=deterministic -> NPU multi-context corruption (vision pollutes policy).")
