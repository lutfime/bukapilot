#!/usr/bin/env python3
"""
Convert the standard openpilot 0.11 driving model (driving_supercombo.onnx) to RKNN
for the Kommu KA2 (Rockchip RK3588 NPU).

MUST RUN ON UBUNTU (RKNN-Toolkit2 is Ubuntu-only). Run via Docker:
    docker run --rm -v "$PWD":/work -w /work ubuntu:22.04 bash -c "
      apt-get update && apt-get install -y python3.10 python3-pip wget \\
      && pip3 install rknn-toolkit2 onnx numpy \\
      && python3 tools-local/convert_011_model_to_rknn.py"

Or on a Linux box / VM with RKNN-Toolkit2 installed:
    python3 tools-local/convert_011_model_to_rknn.py

Output: selfdrive/modeld/models/driving_supercombo.rknn (+ metadata pkl)

Findings from inspecting the model (2026-08-04):
- 481 nodes, opset 20, IR 10 — standard transformer ops (MatMul/Softmax/LayerNorm)
- Only one mildly risky op: GatherND (x1) — usually supported on RK3588
- NO FlashAttention / Attention / GridSample (those would block conversion)
- The output_slices are EMBEDDED in the ONNX metadata as base64 pickle — extracted below
- The model HAS a direct action head (slice 2062-2066)
"""
import os
import sys
import pickle
import base64
from pathlib import Path

import onnx
import numpy as np

try:
  from rknn.api import RKNN
except ImportError:
  print("ERROR: rknn-toolkit2 not installed. Ubuntu-only. See docstring.", file=sys.stderr)
  print("  pip install rknn-toolkit2", file=sys.stderr)
  sys.exit(1)

# --- Configuration ----------------------------------------------------------

# REPO_ROOT: honor env override (needed when running in Docker where the script path's
# parents don't match the repo root). Default: parents[3] of this script (tools-local/x.py ->
# repo root). Inside the container at /work/tools-local/x.py, parents[2]=/ which is wrong,
# so REPO_ROOT_OVERRIDE=/work must be set in the Docker invocation.
REPO_ROOT = Path(os.environ.get("REPO_ROOT_OVERRIDE", "")).resolve() if os.environ.get("REPO_ROOT_OVERRIDE") else Path(__file__).resolve().parents[2]
MODELS_DIR = REPO_ROOT / "selfdrive" / "modeld" / "models"

ONNX_URL = "https://github.com/commaai/openpilot/raw/master/openpilot/selfdrive/modeld/models/driving_supercombo.onnx"
ONNX_PATH = MODELS_DIR / "driving_supercombo.onnx"
RKNN_PATH = MODELS_DIR / "driving_supercombo.rknn"
META_PATH = MODELS_DIR / "driving_supercombo_metadata.pkl"

TARGET_PLATFORM = "rk3588"

# FP16 quantization — Kommu's approach (matches their runtime casting inputs to float16).
# INT8 loses too much precision (RKPilot/sunnypilot-pc rejected RKNN for this reason).
QUANTIZE_FP16 = True

# --- Helpers ----------------------------------------------------------------

def download_onnx_if_missing():
  if ONNX_PATH.exists() and ONNX_PATH.stat().st_size > 10_000_000:
    print(f"[ok] ONNX already present: {ONNX_PATH} ({ONNX_PATH.stat().st_size/1e6:.1f} MB)")
    return
  print(f"[download] fetching {ONNX_URL} (~92 MB)")
  MODELS_DIR.mkdir(parents=True, exist_ok=True)
  import urllib.request
  try:
    urllib.request.urlretrieve(ONNX_URL, ONNX_PATH)
  except Exception:
    # urlretrieve hits LFS pointers on github raw; fall back to curl -L which follows redirects
    import subprocess
    try:
      subprocess.call(["curl", "-sL", ONNX_URL, "-o", str(ONNX_PATH)], check=True)
    except (FileNotFoundError, subprocess.CalledProcessError):
      print(f"[FAIL] could not download (no curl/urllib). Place the model manually at {ONNX_PATH}", file=sys.stderr)
      sys.exit(1)
  if not (ONNX_PATH.exists() and ONNX_PATH.stat().st_size > 10_000_000):
    print(f"[FAIL] download incomplete (size={ONNX_PATH.stat().st_size if ONNX_PATH.exists() else 0})", file=sys.stderr)
    sys.exit(1)
  print(f"[ok] downloaded: {ONNX_PATH} ({ONNX_PATH.stat().st_size/1e6:.1f} MB)")

def extract_metadata_from_onnx():
  """
  The output_slices ship as a base64-encoded pickle in the ONNX metadata_props.
  This matches upstream's make_metadata_dict() in get_model_metadata.py — we read them
  back directly instead of recomputing. Also grabs input_shapes + output_shapes from
  the graph, and the model_checkpoint.
  """
  print(f"\n[metadata] extracting from {ONNX_PATH.name}")
  m = onnx.load(str(ONNX_PATH))

  output_slices_b64 = None
  model_checkpoint = None
  for prop in m.metadata_props:
    if prop.key == 'output_slices':
      output_slices_b64 = prop.value
    elif prop.key == 'model_checkpoint':
      model_checkpoint = prop.value

  if output_slices_b64 is None:
    print("[FAIL] output_slices not found in ONNX metadata", file=sys.stderr)
    sys.exit(1)

  output_slices = pickle.loads(base64.b64decode(output_slices_b64))
  print(f"[ok] model_checkpoint: {model_checkpoint}")
  print(f"[ok] output_slices ({len(output_slices)} entries):")
  for name, sl in output_slices.items():
    size = (sl.stop - sl.start) if hasattr(sl, 'stop') and sl.stop is not None else '?'
    print(f"       {name:30s} {sl}  (size: {size})")

  # input_shapes + output_shapes from the graph
  def shape_of(ts):
    dims = []
    for d in ts.type.tensor_type.shape.dim:
      dims.append(d.dim_value if d.HasField('dim_value') else d.dim_param)
    return tuple(dims)

  input_shapes = {inp.name: shape_of(inp) for inp in m.graph.input}
  output_shapes = {out.name: shape_of(out) for out in m.graph.output}

  metadata = {
    'model_checkpoint': model_checkpoint,
    'output_slices': output_slices,
    'input_shapes': input_shapes,
    'output_shapes': output_shapes,
  }
  return metadata

# --- Main -------------------------------------------------------------------

def cast_uint8_inputs():
  """RKNN-Toolkit2 w16a16i build rejects UINT8 inputs ("Not Support Dtype: 2").
  Cast img/big_img from UINT8 to FLOAT (matches bukapilot runtime which casts to float16)."""
  import subprocess
  ret = subprocess.call([sys.executable, str(Path(__file__).parent / "cast_uint8_inputs_to_float.py"), str(ONNX_PATH)])
  if ret != 0:
    print(f"[FAIL] UINT8 cast failed (exit {ret})", file=sys.stderr)
    sys.exit(1)

def downconvert_opset_if_needed(max_opset: int = 19):
  """RKNN-Toolkit2 v2.3.2 supports ONNX opset <= 19; comma's 0.11 model is opset 20.
  The opset-20-only op is native Gelu (39 instances). rewrite_gelu_for_opset19.py replaces
  each Gelu with its erf-subgraph (Gelu(x) = 0.5*x*(1+erf(x/sqrt(2)))), then we downconvert."""
  m = onnx.load(str(ONNX_PATH))
  current = max((o.version for o in m.opset_import), default=0)
  if current <= max_opset:
    print(f"[ok] opset {current} <= {max_opset}, no downconvert needed")
    return
  print(f"[downconvert] opset {current} -> {max_opset}")
  import subprocess
  ret = subprocess.call([sys.executable, str(Path(__file__).parent / "rewrite_gelu_for_opset19.py"), str(ONNX_PATH)])
  if ret != 0:
    print(f"[FAIL] Gelu rewrite failed (exit {ret})", file=sys.stderr)
    sys.exit(1)
  print(f"[ok] saved as opset {max_opset}")

def main():
  download_onnx_if_missing()
  metadata = extract_metadata_from_onnx()
  cast_uint8_inputs()
  downconvert_opset_if_needed(max_opset=19)

  print(f"\n[convert] {ONNX_PATH.name} -> {RKNN_PATH.name} (target={TARGET_PLATFORM}, fp16={QUANTIZE_FP16})")

  rknn = RKNN(verbose=True)

  # --- config ---
  # The img/big_img inputs were UINT8; cast_uint8_inputs_to_float.py changes them to FLOAT
  # (matching bukapilot's runtime which casts uint8->float16 before feeding). RKNN's w16a16i
  # build rejects UINT8 ("Not Support Dtype: 2"), so the cast is required.
  # mean/std empty: the model handles its own normalization internally once inputs are float.
  rknn.config(
    mean_values=[[]],
    std_values=[[]],
    target_platform=TARGET_PLATFORM,
    quantized_dtype='w16a16i' if QUANTIZE_FP16 else 'w8a8',
    quantized_method='channel',
    optimization_level=3,
  )

  # --- load ONNX ---
  ret = rknn.load_onnx(model=str(ONNX_PATH), outputs=None)
  if ret != 0:
    print(f"[FAIL] load_onnx returned {ret}. Likely an unsupported operator.", file=sys.stderr)
    print("       Verbose log above should name the failing op.", file=sys.stderr)
    print("       Expected OK: only GatherND is mildly risky in this model.", file=sys.stderr)
    sys.exit(1)

  # --- build ---
  # NOTE (2026-08-07): do_quantization=False makes the quantized_dtype setting above INERT.
  # Per Rockchip docs: "do_quantization=False will not perform quantization operations,
  # but will convert the weights from float32 to float16." So this produces an FP16 model,
  # NOT an INT16-quantized model. The empty quant_tab in the output confirms this.
  #
  # On-device benchmark showed FP16 = 138ms inference (7.3 Hz) — too slow for 20Hz.
  # Switching to do_quantization=True with w16a16i would give true INT16 quantization BUT
  # no speedup (FP16 and INT16 share the same 16-bit NPU datapath on RK3588, ~3 TOPS).
  # Only INT8 (w8a8) is ~2x faster (~6 TOPS) but was rejected for driving quality.
  # See MODEL-CONVERSION-GUIDE.md §4 for the full analysis.
  ret = rknn.build(do_quantization=False)
  if ret != 0:
    print(f"[FAIL] build returned {ret}.", file=sys.stderr)
    sys.exit(1)

  # --- export ---
  ret = rknn.export_rknn(str(RKNN_PATH))
  if ret != 0:
    print(f"[FAIL] export_rknn returned {ret}.", file=sys.stderr)
    sys.exit(1)

  print(f"\n[ok] RKNN exported: {RKNN_PATH} ({RKNN_PATH.stat().st_size/1e6:.1f} MB)")

  # --- metadata ---
  with open(META_PATH, "wb") as f:
    pickle.dump(metadata, f)
  print(f"[ok] metadata written: {META_PATH}")

  rknn.release()
  print("\n[DONE] Conversion succeeded. Next steps:")
  print(f"  1. Copy {RKNN_PATH.name} + {META_PATH.name} to the KA2 at selfdrive/modeld/models/")
  print( "  2. Adapt ModelStateRKNN (modeld.py:386) to load the single model:")
  print( "     - Load driving_supercombo.rknn instead of vision+policy split")
  print( "     - Feed all 6 inputs (img, big_img, desire_pulse, traffic_convention, action_t, features_buffer)")
  print( "     - Note: features_buffer is [1,24,512] on 0.11 (was [1,25,512] on 0.10)")
  print( "     - Run once, slice the flat 2580 output per the metadata output_slices")
  print( "  3. Force Python runner for first test: RKNN_USE_PYTHON=1")
  print( "  4. Test on device: does modeld boot? hit 20 Hz? X70 drives sanely?")

if __name__ == "__main__":
  main()
