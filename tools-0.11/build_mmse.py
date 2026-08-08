#!/usr/bin/env python3
"""
Build 0.11 supercombo with MMSE algorithm (best accuracy INT8).
MMSE is slow but produces better quantization scales.
Also builds FP16 with flash attention for comparison.

Output: actual .rknn model files that can be benchmarked on device.
"""
import sys, os, time
import numpy as np
from rknn.api import RKNN

REPO = os.environ.get("REPO_ROOT_OVERRIDE", "/work")
ONNX = os.path.join(REPO, "selfdrive/modeld/models/driving_supercombo.onnx")
DATASET = os.path.join(REPO, "tools-local/calibration_frames/dataset.txt")
MODELS_DIR = os.path.join(REPO, "selfdrive/modeld/models")

def build_model(name, dtype, config_dict, output_name):
  print(f"\n{'='*60}")
  print(f"  Building: {name}")
  print(f"  Output: {output_name}")
  print(f"  Config: {config_dict}")
  print(f"{'='*60}")

  t0 = time.time()
  rknn = RKNN(verbose=False)
  rknn.config(**config_dict)

  ret = rknn.load_onnx(model=ONNX)
  if ret != 0:
    print(f"  ❌ load_onnx failed: {ret}")
    return False

  if dtype == "fp16":
    ret = rknn.build(do_quantization=False)
  else:
    print(f"  Building with dataset={DATASET}...")
    print(f"  (MMSE can take 15-30 minutes, please wait...)")
    ret = rknn.build(do_quantization=True, dataset=DATASET)

  elapsed = time.time() - t0
  if ret != 0:
    print(f"  ❌ build failed: {ret} (after {elapsed:.0f}s)")
    rknn.release()
    return False

  output_path = os.path.join(MODELS_DIR, output_name)
  ret = rknn.export_rknn(output_path)
  if ret != 0:
    print(f"  ❌ export failed: {ret}")
    rknn.release()
    return False

  size_mb = os.path.getsize(output_path) / 1e6
  print(f"  ✅ {output_name}: {size_mb:.1f} MB (built in {elapsed:.0f}s)")
  rknn.release()
  return True

def main():
  base = {
    "mean_values": [[]], "std_values": [[]],
    "target_platform": "rk3588", "optimization_level": 3,
    "float_dtype": "float16",
  }

  results = []

  # 1. FP16 with flash attention + remove reshape (best FP16 config)
  r = build_model(
    "FP16 + flash + rm_reshape",
    "fp16",
    {**base, "enable_flash_attention": True, "remove_reshape": True},
    "driving_supercombo_fp16_flash.rknn"
  )
  results.append(("FP16 + flash + rm_reshape", r))

  # 2. INT8 with normal algorithm + flash attention (baseline INT8 improved)
  r = build_model(
    "INT8 normal + flash + rm_reshape",
    "int8",
    {**base, "quantized_dtype": "w8a8", "quantized_method": "channel",
     "quantized_algorithm": "normal",
     "enable_flash_attention": True, "remove_reshape": True},
    "driving_supercombo_int8_flash.rknn"
  )
  results.append(("INT8 normal + flash + rm_reshape", r))

  # 3. INT8 with MMSE algorithm + flash attention (best accuracy)
  print(f"\n  NOTE: MMSE build takes 15-30 min. Do not timeout.")
  r = build_model(
    "INT8 MMSE + flash + rm_reshape",
    "int8",
    {**base, "quantized_dtype": "w8a8", "quantized_method": "channel",
     "quantized_algorithm": "mmse",
     "enable_flash_attention": True, "remove_reshape": True},
    "driving_supercombo_int8_mmse.rknn"
  )
  results.append(("INT8 MMSE + flash + rm_reshape", r))

  # Summary
  print(f"\n\n{'='*60}")
  print(f"BUILD SUMMARY")
  print(f"{'='*60}")
  for name, success in results:
    status = "✅" if success else "❌"
    print(f"  {status} {name}")

  # List output files
  print(f"\nOutput files:")
  for f in sorted(os.listdir(MODELS_DIR)):
    if f.endswith('.rknn') and 'flash' in f or 'mmse' in f:
      size = os.path.getsize(os.path.join(MODELS_DIR, f)) / 1e6
      print(f"  {f}: {size:.1f} MB")

if __name__ == "__main__":
  main()
