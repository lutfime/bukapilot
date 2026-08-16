#!/usr/bin/env python3
"""
Rewrite Gelu using erf in SUM form: 0.5*x + 0.5*x*erf(x/sqrt(2))
Different node structure than the product form in rewrite_gelu_for_opset19.py
(product form: x * 0.5 * (1+erf(...))). Since the RKNN compiler is
structure-sensitive, the sum form may compile differently.
"""
import sys
import numpy as np
import onnx
from onnx import helper, TensorProto

INV_SQRT2 = 0.7071067811865476

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
        # out = 0.5*x + 0.5*(x * erf(x*invsqrt2))
        new_nodes.extend([
            make_const(f"{base}_invsqrt2", INV_SQRT2),
            helper.make_node("Mul", [x, f"{base}_invsqrt2"], [f"{base}_xs"], name=f"{base}_scale"),
            helper.make_node("Erf", [f"{base}_xs"], [f"{base}_erf"], name=f"{base}_erf"),
            helper.make_node("Mul", [x, f"{base}_erf"], [f"{base}_xe"], name=f"{base}_mulx"),
            make_const(f"{base}_half", 0.5),
            helper.make_node("Mul", [f"{base}_xe", f"{base}_half"], [f"{base}_hxе"], name=f"{base}_mulh"),
            make_const(f"{base}_half2", 0.5),
            helper.make_node("Mul", [x, f"{base}_half2"], [f"{base}_hx"], name=f"{base}_mulh2"),
            helper.make_node("Add", [f"{base}_hx", f"{base}_hxе"], [out], name=f"{base}_add"),
        ])
    del graph.node[:]
    graph.node.extend(new_nodes)
    return replaced

if __name__ == "__main__":
    path = sys.argv[1]
    print(f"[load] {path}")
    m = onnx.load(path)
    n = replace_gelu_nodes(m)
    print(f"[rewrite] replaced {n} Gelu nodes with erf-sum-form")
    from onnx import version_converter
    m19 = version_converter.convert_version(m, 19)
    print(f"[downconvert] now opset {max(o.version for o in m19.opset_import)}")
    onnx.save(m19, path)
    print(f"[ok] saved {path}")
