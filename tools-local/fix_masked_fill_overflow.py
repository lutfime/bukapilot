#!/usr/bin/env python3
"""Fix fp16 overflow: replace -inf in masked_fill attention mask with -10000.

Root cause: the RK3588 NPU computes in true hardware fp16. The attention mask's
-inf value causes non-deterministic inf/NaN on ~9% of frames. ONNX Runtime is
unaffected because it upcasts to fp32 internally.

Fix: replace -inf with -10000. softmax(-10000) = 0 = same masking effect.
-10000 is well within fp16 range. Zero accuracy loss.

Usage: python3 fix_masked_fill_overflow.py model.onnx
"""
import sys
import numpy as np
import onnx
from onnx import helper, TensorProto

MASK_VALUE = -10000.0  # safe fp16 value, softmax(-10000) = 0

def fix_masked_fill(model_path):
    m = onnx.load(model_path)
    fixed = 0
    
    # Approach 1: Find Constant nodes producing -inf or very large negative values
    # These are typically the mask values fed to Add (input + mask) or Where
    for node in m.graph.node:
        if node.op_type == "Constant":
            for attr in node.attribute:
                if attr.name == "value" and attr.type == onnx.AttributeProto.TENSOR:
                    tensor = attr.t
                    if tensor.data_type == TensorProto.FLOAT:
                        # Read float data
                        if tensor.float_data:
                            vals = list(tensor.float_data)
                        elif tensor.raw_data:
                            vals = np.frombuffer(tensor.raw_data, dtype=np.float32).tolist()
                        else:
                            continue
                        
                        changed = False
                        for i, v in enumerate(vals):
                            if np.isinf(v) and v < 0:
                                vals[i] = MASK_VALUE
                                changed = True
                        
                        if changed:
                            # Write back
                            if tensor.float_data:
                                del tensor.float_data[:]
                                tensor.float_data.extend(vals)
                            else:
                                tensor.raw_data = np.array(vals, dtype=np.float32).tobytes()
                            fixed += 1
                            print(f"  Fixed Constant '{node.name}': -inf -> {MASK_VALUE}")
                    
                    elif tensor.data_type == TensorProto.FLOAT16:
                        if tensor.int32_data:
                            vals = list(tensor.int32_data)
                        elif tensor.raw_data:
                            vals = np.frombuffer(tensor.raw_data, dtype=np.uint16).tolist()
                        else:
                            continue
                        
                        changed = False
                        for i, v in enumerate(vals):
                            # fp16 -inf = 0xFC00 (unsigned 64512)
                            if v == 0xFC00 or v == 64512:
                                # -10000 in fp16 = 0xC61C (sign + exponent + mantissa)
                                import struct
                                fp16_val = struct.unpack('H', struct.pack('e', np.float16(MASK_VALUE)))[0]
                                vals[i] = fp16_val
                                changed = True
                        
                        if changed:
                            if tensor.int32_data:
                                del tensor.int32_data[:]
                                tensor.int32_data.extend(vals)
                            else:
                                tensor.raw_data = np.array(vals, dtype=np.uint16).tobytes()
                            fixed += 1
                            print(f"  Fixed Constant '{node.name}' (fp16): -inf -> {MASK_VALUE}")
    
    # Approach 2: Find Where/Mul nodes that use -inf from initializers
    for init in m.graph.initializer:
        if init.data_type == TensorProto.FLOAT:
            if init.float_data:
                vals = list(init.float_data)
            elif init.raw_data:
                vals = np.frombuffer(init.raw_data, dtype=np.float32).tolist()
            else:
                continue
            changed = False
            for i, v in enumerate(vals):
                if np.isinf(v) and v < 0:
                    vals[i] = MASK_VALUE
                    changed = True
            if changed:
                if init.float_data:
                    del init.float_data[:]
                    init.float_data.extend(vals)
                else:
                    init.raw_data = np.array(vals, dtype=np.float32).tobytes()
                fixed += 1
                print(f"  Fixed initializer '{init.name}': -inf -> {MASK_VALUE}")
    
    onnx.save(m, model_path)
    print(f"  Total fixes: {fixed}")
    return fixed

if __name__ == "__main__":
    path = sys.argv[1]
    print(f"[load] {path}")
    n = fix_masked_fill(path)
    print(f"[ok] saved {path} ({n} constants fixed)")
