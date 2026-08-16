#!/usr/bin/env python3
"""
Rewrite Gelu using the tanh approximation (GPT-2 standard):
  Gelu(x) ≈ 0.5 * x * (1 + tanh( sqrt(2/pi) * (x + 0.044715 * x^3) ))

Why: our erf-subgraph rewrite is the ONLY structure ever tested on the NPU, and the
S=8 experiment proved the RKNN compiler miscompiles unexpected graph structures.
This variant uses the Tanh kernel instead of Erf — a completely different NPU
implementation path. If the Erf lowering is the miscompiled part, this avoids it.

Accuracy: max abs error ~0.003 vs exact Gelu (better than the sigmoid approx we
already accepted at 0.027 mean). ~10 nodes per Gelu.

Run on an opset-20 ONNX to produce opset 19.
"""
import sys
import numpy as np
import onnx
from onnx import helper, TensorProto

C1 = 0.7978845608   # sqrt(2/pi)
C2 = 0.044715       # cubic coefficient

def make_const(name, value):
    return helper.make_node("Constant", [], [name],
        value=helper.make_tensor(f"{name}_v", TensorProto.FLOAT, [], [value]))

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
        # x2 = x * x ; x3 = x2 * x ; inner = x + C2*x3 ; scaled = C1*inner
        # t = tanh(scaled) ; out = (0.5*x) * (1 + t)
        new_nodes.extend([
            helper.make_node("Mul", [x, x], [f"{base}_x2"], name=f"{base}_sq1"),
            helper.make_node("Mul", [f"{base}_x2", x], [f"{base}_x3"], name=f"{base}_sq2"),
            make_const(f"{base}_c2", C2),
            helper.make_node("Mul", [f"{base}_x3", f"{base}_c2"], [f"{base}_cx3"], name=f"{base}_cub"),
            helper.make_node("Add", [x, f"{base}_cx3"], [f"{base}_inner"], name=f"{base}_in"),
            make_const(f"{base}_c1", C1),
            helper.make_node("Mul", [f"{base}_inner", f"{base}_c1"], [f"{base}_scaled"], name=f"{base}_sc"),
            helper.make_node("Tanh", [f"{base}_scaled"], [f"{base}_t"], name=f"{base}_tanh"),
            make_const(f"{base}_one", 1.0),
            helper.make_node("Add", [f"{base}_one", f"{base}_t"], [f"{base}_1pt"], name=f"{base}_add1"),
            make_const(f"{base}_half", 0.5),
            helper.make_node("Mul", [x, f"{base}_half"], [f"{base}_xh"], name=f"{base}_hx"),
            helper.make_node("Mul", [f"{base}_xh", f"{base}_1pt"], [out], name=f"{base}_mulx"),
        ])
    del graph.node[:]
    graph.node.extend(new_nodes)
    return replaced

if __name__ == "__main__":
    path = sys.argv[1]
    print(f"[load] {path}")
    m = onnx.load(path)
    n = replace_gelu_nodes(m)
    print(f"[rewrite] replaced {n} Gelu nodes with tanh-approximation (13 nodes each)")
    from onnx import version_converter
    m19 = version_converter.convert_version(m, 19)
    new_opset = max(o.version for o in m19.opset_import)
    print(f"[downconvert] now opset {new_opset}")
    onnx.save(m19, path)
    print(f"[ok] saved {path}")
