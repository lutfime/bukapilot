#!/usr/bin/env python3
"""
Inspect the openpilot 0.11 driving_supercombo.onnx to map its input/output structure.
This informs the RKNN conversion metadata (output_slices) and confirms whether the model
has a direct 'action' head (vs plan-derivation only).

Does NOT require RKNN — pure onnx/protobuf. Runs natively on macOS.
"""
import sys
from pathlib import Path

try:
  import onnx
except ImportError:
  print("ERROR: pip install onnx", file=sys.stderr); sys.exit(1)

import urllib.request

MODEL_DIR = Path(__file__).resolve().parents[2] / "selfdrive" / "modeld" / "models"
ONNX_URL = "https://raw.githubusercontent.com/commaai/openpilot/master/openpilot/selfdrive/modeld/models/driving_supercombo.onnx"
ONNX_PATH = MODEL_DIR / "driving_supercombo_inspect.onnx"  # distinct name so we don't clobber anything

def fetch():
  MODEL_DIR.mkdir(parents=True, exist_ok=True)
  if ONNX_PATH.exists():
    print(f"[ok] cached: {ONNX_PATH} ({ONNX_PATH.stat().st_size/1e6:.1f} MB)")
    return
  print(f"[download] {ONNX_URL}")
  urllib.request.urlretrieve(ONNX_URL, ONNX_PATH)
  print(f"[ok] downloaded: {ONNX_PATH.stat().st_size/1e6:.1f} MB)")

def main():
  fetch()
  print(f"\n[load] {ONNX_PATH.name}")
  m = onnx.load(str(ONNX_PATH))

  print(f"\n=== IR version: {m.ir_version} | opset: {[o.version for o in m.opset_import]} ===")
  print(f"=== producer: {m.producer_name} ===")

  print("\n--- INPUTS ---")
  for inp in m.graph.input:
    dims = [d.dim_value if d.HasField('dim_value') else d.dim_param for d in inp.type.tensor_type.shape.dim]
    dtype = inp.type.tensor_type.elem_type  # 1=float, 7=int64, etc
    print(f"  {inp.name:30s} dtype={dtype} shape={dims}")

  print("\n--- OUTPUTS ---")
  for out in m.graph.output:
    dims = [d.dim_value if d.HasField('dim_value') else d.dim_param for d in out.type.tensor_type.shape.dim]
    dtype = out.type.tensor_type.elem_type
    print(f"  {out.name:30s} dtype={dtype} shape={dims}")

  # Look specifically for an 'action' output — the 0.11 signature
  out_names = [o.name for o in m.graph.output]
  print("\n--- ACTION HEAD CHECK ---")
  if any('action' in n.lower() for n in out_names):
    print(f"  ✅ FOUND action head: {[n for n in out_names if 'action' in n.lower()]}")
    print("  → get_action_from_model's 'else' branch will be used")
  else:
    print(f"  ℹ️  no explicit 'action' output; outputs are: {out_names}")
    print("  → get_action_from_model's plan-derivation path will be used (still works)")

  # Operator census — flag ops known to be problematic on RK3588 NPU
  print("\n--- OPERATOR CENSUS (RKNN compatibility risk flags) ---")
  from collections import Counter
  ops = Counter(n.op_type for n in m.graph.node)
  risky = {"Attention", "FlashAttention", "GridSample", "DynamicQuantizeLinear",
           "Trilu", "Roll", "Erf"}  # erf is in GELU — usually fine but watch
  for op, count in sorted(ops.items(), key=lambda x: -x[1]):
    flag = " ⚠️ risky" if op in risky else ""
    print(f"  {op:30s} x{count}{flag}")

  print(f"\n=== Total nodes: {len(m.graph.node)} | total params size: {ONNX_PATH.stat().st_size/1e6:.1f} MB ===")

if __name__ == "__main__":
  main()
