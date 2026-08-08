#!/usr/bin/env python3
"""
Build ALL supercombo configs with good parameters AND SAVE the .rknn files.
Every model that gets built gets saved to disk. No more throwing away results.

Models to build:
1. FP16 + flash_attn + rm_reshape
2. INT8 normal + flash_attn + rm_reshape
3. INT8 MMSE + flash_attn + rm_reshape (if RAM allows)

Usage in Docker:
  python3 tools-local/build_and_save_all.py
"""
import sys, os, time, shutil
import numpy as np
from rknn.api import RKNN

REPO = os.environ.get("REPO_ROOT_OVERRIDE", "/work")
ONNX = os.path.join(REPO, "selfdrive/modeld/models/driving_supercombo.onnx")
DATASET = os.path.join(REPO, "tools-local/calibration_frames/dataset.txt")
MODELS_DIR = os.path.join(REPO, "selfdrive/modeld/models")
STORE_DIR = os.path.join(REPO, "tools-local/models_store")

os.makedirs(STORE_DIR, exist_ok=True)

def build_and_save(name, dtype, config_dict, output_name):
  """Build a model and SAVE it to both models dir and store."""
  print(f"\n{'='*60}")
  print(f"  BUILD: {name}")
  print(f"  SAVE: {output_name}")
  print(f"{'='*60}")

  t0 = time.time()
  rknn = RKNN(verbose=False)
  rknn.config(**config_dict)

  ret = rknn.load_onnx(model=ONNX)
  if ret != 0:
    print(f"  ❌ load_onnx: {ret}")
    return False

  if dtype == "fp16":
    ret = rknn.build(do_quantization=False)
  else:
    print(f"  Quantizing with dataset (may take 15-30 min for MMSE)...")
    ret = rknn.build(do_quantization=True, dataset=DATASET)

  elapsed = time.time() - t0
  if ret != 0:
    print(f"  ❌ build: {ret} ({elapsed:.0f}s)")
    rknn.release()
    return False

  # SAVE to models dir
  out_path = os.path.join(MODELS_DIR, output_name)
  ret = rknn.export_rknn(out_path)
  if ret != 0:
    print(f"  ❌ export: {ret}")
    rknn.release()
    return False

  # Also copy to store
  store_path = os.path.join(STORE_DIR, output_name)
  shutil.copy2(out_path, store_path)

  size_mb = os.path.getsize(out_path) / 1e6
  print(f"  ✅ {output_name}: {size_mb:.1f} MB ({elapsed:.0f}s)")
  print(f"     Saved to: {out_path}")
  print(f"     Saved to: {store_path}")
  rknn.release()
  return True

def main():
  base = {
    "mean_values": [[]], "std_values": [[]],
    "target_platform": "rk3588", "optimization_level": 3,
    "float_dtype": "float16",
    "enable_flash_attention": True,
    "remove_reshape": True,
  }

  results = []

  # 1. FP16 + flash + rm_reshape
  r = build_and_save(
    "FP16 + flash_attn + rm_reshape",
    "fp16",
    {**base},
    "driving_supercombo_fp16_flash.rknn"
  )
  results.append(("FP16 + flash + rm_reshape", r, "driving_supercombo_fp16_flash.rknn"))

  # 2. INT8 normal + flash + rm_reshape
  r = build_and_save(
    "INT8 normal + flash + rm_reshape",
    "int8",
    {**base, "quantized_dtype": "w8a8", "quantized_method": "channel",
     "quantized_algorithm": "normal"},
    "driving_supercombo_int8_flash.rknn"
  )
  results.append(("INT8 normal + flash + rm_reshape", r, "driving_supercombo_int8_flash.rknn"))

  # 3. INT8 MMSE + flash + rm_reshape (best accuracy, slow)
  print(f"\n  NOTE: MMSE takes 15-30 min. Be patient.")
  r = build_and_save(
    "INT8 MMSE + flash + rm_reshape",
    "int8",
    {**base, "quantized_dtype": "w8a8", "quantized_method": "channel",
     "quantized_algorithm": "mmse"},
    "driving_supercombo_int8_mmse_flash.rknn"
  )
  results.append(("INT8 MMSE + flash + rm_reshape", r, "driving_supercombo_int8_mmse_flash.rknn"))

  # Summary
  print(f"\n\n{'='*60}")
  print(f"BUILD COMPLETE - ALL SAVED MODELS")
  print(f"{'='*60}")
  for name, success, filename in results:
    status = "✅" if success else "❌"
    path = os.path.join(STORE_DIR, filename)
    if os.path.exists(path):
      size = os.path.getsize(path) / 1e6
      print(f"  {status} {name}: {filename} ({size:.1f} MB)")
    else:
      print(f"  {status} {name}: NOT SAVED")

if __name__ == "__main__":
  main()
