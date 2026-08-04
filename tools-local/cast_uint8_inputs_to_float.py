#!/usr/bin/env python3
"""
Cast UINT8 image inputs (img, big_img) to FLOAT in the ONNX graph so RKNN-Toolkit2 can
build the model under w16a16i (which rejects UINT8). The model's internal preprocessing
handles the float math; we just change the declared input type.

Run BEFORE convert_011_model_to_rknn.py if the model has UINT8 inputs.
"""
import sys
import onnx
from onnx import helper, TensorProto

def main():
    path = sys.argv[1] if len(sys.argv) > 1 else "selfdrive/modeld/models/driving_supercombo.onnx"
    print(f"[load] {path}")
    m = onnx.load(path)

    changed = 0
    for inp in m.graph.input:
        if inp.type.tensor_type.elem_type == TensorProto.UINT8:
            inp.type.tensor_type.elem_type = TensorProto.FLOAT
            changed += 1
            print(f"  cast {inp.name}: UINT8 -> FLOAT")

    if changed:
        onnx.save(m, path)
        print(f"[ok] {changed} input(s) cast to FLOAT, saved {path}")
    else:
        print("[ok] no UINT8 inputs found, nothing to do")

if __name__ == "__main__":
    main()
