#!/usr/bin/env python3
"""
Calibration dataset generator for INT8 quantization of driving_supercombo.

RKNN-Toolkit2's build(do_quantization=True, dataset='dataset.txt') for multi-input
models expects EACH LINE to list ALL inputs space-separated, in ONNX input order.

Model inputs (from the ONNX), in order:
  1. img               [1, 12, 128, 256]  float  (narrow road cam)
  2. big_img           [1, 12, 128, 256]  float  (wide road cam)
  3. desire_pulse      [1, 25, 8]         float
  4. traffic_convention [1, 2]            float
  5. action_t          [1, 2]             float
  6. features_buffer   [1, 24, 512]       float

Each input gets its own .npy file. Each dataset.txt line = 6 paths.
"""
import os, glob
import numpy as np
from PIL import Image

REPO = os.environ.get("REPO_ROOT_OVERRIDE", "/Users/lutfimacmini/Documents/Project/bukapilot")
EXTRACTED = f"{REPO}/tools-local/calibration_frames/extracted"
OUTPUT_DIR = f"{REPO}/tools-local/calibration_frames/calib_npy"
DATASET_TXT = f"{REPO}/tools-local/calibration_frames/dataset.txt"

IMG_SHAPE = (1, 12, 128, 256)

def load_and_transform(img_path):
  """Load JPEG → 128×256 grayscale → 12-channel temporal stack for calibration."""
  im = Image.open(img_path).convert("L")
  im = im.resize((256, 128), Image.BILINEAR)
  arr = np.asarray(im, dtype=np.float32)  # [128, 256], 0-255
  single = arr.reshape(1, 1, 128, 256)
  temporal = np.tile(single, (1, 6, 1, 1))   # [1, 6, 128, 256] — 6-frame temporal approx
  stacked = np.tile(temporal, (1, 2, 1, 1))  # [1, 12, 128, 256]
  return stacked

def main():
  os.makedirs(OUTPUT_DIR, exist_ok=True)
  # Clear old output
  for f in glob.glob(f"{OUTPUT_DIR}/*"):
    os.remove(f)

  # Match fcamera + ecamera frames from the same segment+framenumber
  segments = {}
  for d in sorted(glob.glob(f"{EXTRACTED}/*/")):
    seg = os.path.basename(d.rstrip("/"))
    base = seg.rsplit("_", 1)[0]  # d_2026-08-05--14-04-21__s12
    cam = seg.rsplit("_", 1)[1]   # fcamera or ecamera
    segments.setdefault(base, {})
    for f in sorted(glob.glob(f"{d}frame_*.jpg")):
      fname = os.path.basename(f)
      segments[base].setdefault(fname, {})[cam] = f

  # Build pairs where both cameras are available
  pairs = []
  for base, frames in sorted(segments.items()):
    for fname, cams in sorted(frames.items()):
      if 'fcamera' in cams and 'ecamera' in cams:
        pairs.append((cams['fcamera'], cams['ecamera']))

  print(f"Found {len(pairs)} frame pairs (both cameras)")
  print(f"Using ALL {len(pairs)} pairs (more samples = better quantization accuracy)")

  # Precompute the constant inputs once
  desire = np.zeros((1, 25, 8), dtype=np.float32)
  tc = np.array([[1.0, 0.0]], dtype=np.float32)
  at = np.zeros((1, 2), dtype=np.float32)
  fb = np.zeros((1, 24, 512), dtype=np.float32)

  # Save each input as separate .npy, build dataset.txt
  lines = []
  for i, (fcam, ecam) in enumerate(pairs):
    img = load_and_transform(fcam)
    big_img = load_and_transform(ecam)

    paths = []
    for name, arr in [('img', img), ('big_img', big_img), ('desire_pulse', desire),
                       ('traffic_convention', tc), ('action_t', at), ('features_buffer', fb)]:
      p = f"{OUTPUT_DIR}/s{i:04d}_{name}.npy"
      np.save(p, arr)
      paths.append(p)

    lines.append(" ".join(paths))
    if (i+1) % 50 == 0:
      print(f"  generated {i+1}/{len(pairs)}")

  with open(DATASET_TXT, "w") as f:
    f.write("\n".join(lines) + "\n")

  print(f"\nDone: {len(pairs)} samples")
  print(f"  NPY files: {OUTPUT_DIR}/ ({len(pairs)*6} files)")
  print(f"  Dataset list: {DATASET_TXT}")
  print(f"\nSample dataset.txt line:")
  print(f"  {lines[0]}")

if __name__ == "__main__":
  main()
