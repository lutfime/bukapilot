#!/usr/bin/env python3
"""Dump REAL in-distribution calibration data for policy-head quantization.

Run ON DEVICE. Builds features_buffer windows exactly like production
(same construction as test_variants_real_features.py) and saves them in
RKNN dataset.txt format (one line per sample, npy paths).

Usage:
  cd /data/openpilot && /usr/local/venv/bin/python tools-local/dump_calibration_features.py \
      [--samples 100] [--outdir /data/calib]

Output: <outdir>/dataset.txt + s000_dp.npy/s000_tc.npy/s000_fb.npy ... files.
Copy the whole dir back to the Mac for conversion.
"""
import argparse
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, "/data/openpilot")
from selfdrive.modeld.runners.driving_3split_rknn import Driving3SplitRKNNRunner  # noqa: E402

MD = "/data/openpilot/selfdrive/modeld/models/"
HIDDEN_SLICE = slice(117, 629)
CONTEXT = 25
IMG_SHAPE = (1, 12, 128, 256)

def main():
  ap = argparse.ArgumentParser()
  ap.add_argument("--samples", type=int, default=100)
  ap.add_argument("--outdir", type=str, default="/data/calib")
  args = ap.parse_args()

  outdir = Path(args.outdir)
  outdir.mkdir(parents=True, exist_ok=True)

  desire = np.zeros((1, 25, 8), dtype=np.float32)
  tc = np.array([[1.0, 0.0]], dtype=np.float32)

  runner = Driving3SplitRKNNRunner(MD)
  rng = np.random.RandomState(20260816)
  hidden = []
  lines = []
  n = 0
  while n < args.samples:
    img = np.ascontiguousarray(rng.randint(0, 256, IMG_SHAPE, dtype=np.uint8))
    v = np.asarray(runner.run_vision(img, img), dtype=np.float32).reshape(-1)
    hidden.append(v[HIDDEN_SLICE].copy())
    if len(hidden) >= CONTEXT:
      fb = np.stack(hidden[-CONTEXT:]).reshape(1, 25, 512).astype(np.float32)
      sdir = outdir / ("s%03d" % n)
      sdir.mkdir(exist_ok=True)
      np.save(str(sdir / "dp.npy"), desire)
      np.save(str(sdir / "tc.npy"), tc)
      np.save(str(sdir / "fb.npy"), fb)
      lines.append("%s %s %s" % (sdir / "dp.npy", sdir / "tc.npy", sdir / "fb.npy"))
      n += 1
      if n % 20 == 0:
        print("dumped %d/%d" % (n, args.samples))

  with open(str(outdir / "dataset.txt"), "w") as f:
    f.write("\n".join(lines) + "\n")
  allh = np.stack(hidden)
  print("DONE: %d samples in %s (hidden range [%.2f, %.2f])" %
        (args.samples, outdir, allh.min(), allh.max()))

if __name__ == "__main__":
  main()
