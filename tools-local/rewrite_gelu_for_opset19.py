#!/usr/bin/env python3
"""
Rewrite native Gelu ops (added in opset 20) into the erf-based equivalent subgraph so the
model can be downconverted to opset 19 for RKNN-Toolkit2 (which supports <= 19).

Gelu(x) = 0.5 * x * (1 + erf(x / sqrt(2)))

Run this BEFORE convert_011_model_to_rknn.py when the model is opset 20.
"""
import sys
from pathlib import Path
import onnx
from onnx import helper, TensorProto

# Gelu(x) = 0.5 * x * (1 + erf(x / sqrt(2)))
# Subgraph nodes (per Gelu):
#   Constant (value=0.5)              -> named <base>_c_half
#   Constant (value=1.0)              -> <base>_c_one
#   Constant (value=sqrt(0.5)=0.7071) -> <base>_c_invsqrt2
#   Mul (x, invsqrt2)                 -> <base>_scaled
#   Erf (scaled)                      -> <base>_erf
#   Add (erf, one)                    -> <base>_plus
#   Mul (half, plus)                  -> <base>_gate
#   Mul (gate, x)                     -> replaces original Gelu output

SQRT_HALF = 0.7071067811865476  # 1/sqrt(2)

def make_const(name, value):
    import numpy as np
    return helper.make_node("Constant", [], [name], value=helper.make_tensor(f"{name}_v", TensorProto.FLOAT, [], [value]))

def replace_gelu_nodes(model):
    graph = model.graph
    gelu_nodes = [n for n in graph.node if n.op_type == "Gelu"]
    if not gelu_nodes:
        return 0
    new_nodes = []
    replaced = 0
    for node in graph.node:
        if node.op_type != "Gelu":
            new_nodes.append(node)
            continue
        replaced += 1
        base = node.name or f"gelu_{replaced}"
        x = node.input[0]
        out = node.output[0]
        # Build the 8 replacement nodes (constants + ops). Order matters for topological sort.
        new_nodes.extend([
            make_const(f"{base}_half", 0.5),
            make_const(f"{base}_one", 1.0),
            make_const(f"{base}_invsqrt2", SQRT_HALF),
            helper.make_node("Mul", [x, f"{base}_invsqrt2"], [f"{base}_scaled"], name=f"{base}_scale"),
            helper.make_node("Erf", [f"{base}_scaled"], [f"{base}_erf"], name=f"{base}_erf"),
            helper.make_node("Add", [f"{base}_erf", f"{base}_one"], [f"{base}_plus"], name=f"{base}_addone"),
            helper.make_node("Mul", [f"{base}_half", f"{base}_plus"], [f"{base}_gate"], name=f"{base}_mulgate"),
            helper.make_node("Mul", [f"{base}_gate", x], [out], name=f"{base}_mulx"),
        ])
    del graph.node[:]
    graph.node.extend(new_nodes)
    return replaced

def main():
    path = sys.argv[1] if len(sys.argv) > 1 else "selfdrive/modeld/models/driving_supercombo.onnx"
    print(f"[load] {path}")
    m = onnx.load(path)
    n = replace_gelu_nodes(m)
    print(f"[rewrite] replaced {n} Gelu nodes with erf-subgraphs")
    # Bump down the opset to 19 now that the only opset-20-only op is gone
    from onnx import version_converter
    m19 = version_converter.convert_version(m, 19)
    new_opset = max(o.version for o in m19.opset_import)
    print(f"[downconvert] now opset {new_opset}")
    onnx.save(m19, path)
    print(f"[ok] saved {path} as opset {new_opset}")

if __name__ == "__main__":
    main()
