#!/usr/bin/env python3
"""
Fix 0.10 vision ONNX for RKNN conversion: zero-size 'pad' initializer.
The 'pad' tensor is FLOAT16 (data_type=10) with dims [1, 0] (zero elements).
RKNN's quantizer crashes on np.min([]).

Fix: replace with [1, 1] FLOAT16 tensor containing 0.0.
Also cast UINT8 inputs to FLOAT for RKNN build compatibility.
"""
import onnx, struct, numpy as np
from onnx import TensorProto

ONNX_PATH = "/work/selfdrive/modeld/models/driving_vision.onnx"
FIXED_PATH = "/work/selfdrive/modeld/models/driving_vision_fixed.onnx"

# ONNX data type → (numpy dtype, struct format)
DTYPE_MAP = {
    1: (np.float32, 'f'),    # FLOAT
    10: (np.float16, 'e'),   # FLOAT16
    11: (np.float64, 'd'),   # DOUBLE
    6: (np.int32, 'i'),      # INT32
    7: (np.int64, 'q'),      # INT64
    3: (np.int8, 'b'),       # INT8
    2: (np.uint8, 'B'),      # UINT8
}

m = onnx.load(ONNX_PATH)

# Fix zero-size initializers
for i, init in enumerate(m.graph.initializer):
    dims = list(init.dims)
    if 0 in dims:
        new_dims = [max(1, d) for d in dims]
        total = 1
        for d in new_dims:
            total *= d

        dt = init.data_type
        np_dt, fmt = DTYPE_MAP.get(dt, (np.float32, 'f'))

        # Create numpy array of zeros with correct dtype, convert to raw bytes
        arr = np.zeros(total, dtype=np_dt)
        raw = arr.tobytes()

        new_init = TensorProto()
        new_init.name = init.name
        new_init.data_type = dt  # keep same dtype
        new_init.dims.extend(new_dims)
        new_init.raw_data = raw
        m.graph.initializer[i].CopyFrom(new_init)
        print(f"Fixed {init.name}: dims={dims} dtype={dt} -> dims={new_dims} ({total} elems, {len(raw)} bytes)")

# Cast UINT8 inputs to FLOAT
for inp in m.graph.input:
    if inp.type.tensor_type.elem_type == 2:  # UINT8
        inp.type.tensor_type.elem_type = 1   # FLOAT
        print(f"Cast input {inp.name}: UINT8 -> FLOAT")

onnx.save(m, FIXED_PATH)
print(f"Saved: {FIXED_PATH}")
