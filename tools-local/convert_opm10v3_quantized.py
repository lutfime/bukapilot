#!/usr/bin/env python3
"""Convert OPM10V3 policy heads with TRUE quantization (not pure fp16).

Builds TWO variants (run in Docker, aarch64 RKNN toolkit):
  a) w8a8  + mmse    + real calibration  -> fixes INT8-zeros suspects
  b) w16a16i + channel + real calibration -> vendor fp16-overflow fix (INT16)

Prerequisite: real calibration dir from dump_calibration_features.py
(copy from device). Set CALIB_DIR env or pass --calib.

Usage:
  CALIB_DIR=/path/to/calib python3 tools-local/convert_opm10v3_quantized.py [--variant both|w8a8|w16a16i]
"""
import argparse
import os
import pickle
import base64
import subprocess
import sys
import shutil
from pathlib import Path

import numpy as np
import onnx

MODELS_DIR = Path("selfdrive/modeld/models")
ONNX_STORE = Path("tools-local/models_store/opm10v3-onnx-original")

def extract_meta(p):
  m = onnx.load(p)
  os_b64 = ck = None
  for prop in m.metadata_props:
    if prop.key == "output_slices": os_b64 = prop.value
    elif prop.key == "model_checkpoint": ck = prop.value
  osl = pickle.loads(base64.b64decode(os_b64))
  sh = lambda ts: tuple(d.dim_value if d.HasField("dim_value") else d.dim_param for d in ts.type.tensor_type.shape.dim)
  return {"model_checkpoint": ck, "output_slices": osl,
          "input_shapes": {i.name: sh(i) for i in m.graph.input},
          "output_shapes": {o.name: sh(o) for o in m.graph.output}}

def convert(head, dtype, method, calib_dir, suffix):
  from rknn.api import RKNN
  onnx_f = "driving_%s.onnx" % head
  rknn_f = "driving_%s_opm10v3_%s.rknn" % (head, suffix)
  meta_f = "driving_%s_opm10v3_%s_metadata.pkl" % (head, suffix)
  onnx_p = str(MODELS_DIR / onnx_f)
  print("=== %s [%s + %s] ===" % (head, dtype, method))
  meta = extract_meta(onnx_p)
  rknn = RKNN(verbose=False)
  # NOTE: do_quantization=True — the dtype is now actually applied (unlike all
  # previous conversions where do_quantization=False shipped pure fp16).
  rknn.config(mean_values=[[]], std_values=[[]], target_platform="rk3588",
              quantized_dtype=dtype, quantized_method=method, optimization_level=3)
  if rknn.load_onnx(model=onnx_p, outputs=None) != 0:
    print("  FAIL load"); return False
  if rknn.build(do_quantization=True, dataset=str(Path(calib_dir) / "dataset.txt")) != 0:
    print("  FAIL build"); return False
  # sanity: simulator non-zero (weak signal, but catches zeros-on-sim)
  if rknn.init_runtime(target=None) == 0:
    dp = np.zeros((1, 25, 8), dtype=np.float16)
    tc = np.array([[1., 0.]], dtype=np.float16)
    fb = (np.random.RandomState(99).randn(1, 25, 512) * 0.3).astype(np.float16)
    out = rknn.inference(inputs=[dp, tc, fb])[0]
    mx = float(np.abs(out.astype(np.float64)).max())
    print("  sim: max=%.4f nonzero=%d/%d %s" % (mx, np.count_nonzero(out), out.size,
          "OK" if mx > 1e-3 else "ZEROS!!"))
  if rknn.export_rknn(str(MODELS_DIR / rknn_f)) != 0:
    print("  FAIL export"); return False
  rknn.release()
  with open(str(MODELS_DIR / meta_f), "wb") as f:
    pickle.dump(meta, f)
  print("  DONE: %s (%.1f MB)" % (rknn_f, os.path.getsize(str(MODELS_DIR / rknn_f)) / 1e6))
  return True

if __name__ == "__main__":
  ap = argparse.ArgumentParser()
  ap.add_argument("--calib", default=os.environ.get("CALIB_DIR", "/calib"))
  ap.add_argument("--variant", choices=["both", "w8a8", "w16a16i"], default="both")
  args = ap.parse_args()

  if not (Path(args.calib) / "dataset.txt").exists():
    print("ERROR: %s/dataset.txt not found — dump real calibration data on device first" % args.calib)
    sys.exit(1)

  # Prepare erf-rewritten ONNX (production pipeline)
  for head in ["on_policy", "off_policy"]:
    src = str(ONNX_STORE / ("driving_%s.onnx" % head))
    dst = str(MODELS_DIR / ("driving_%s.onnx" % head))
    shutil.copy(src, dst)
    subprocess.call([sys.executable, "tools-local/rewrite_gelu_for_opset19.py", dst],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

  ok = True
  if args.variant in ("both", "w8a8"):
    for head in ["on_policy", "off_policy"]:
      ok &= convert(head, "w8a8", "channel", args.calib, "q8")
  if args.variant in ("both", "w16a16i"):
    for head in ["on_policy", "off_policy"]:
      ok &= convert(head, "w16a16i", "channel", args.calib, "q16")

  for head in ["on_policy", "off_policy"]:
    p = MODELS_DIR / ("driving_%s.onnx" % head)
    if p.exists(): p.unlink()
  print("ALL OK" if ok else "SOME FAILED")
