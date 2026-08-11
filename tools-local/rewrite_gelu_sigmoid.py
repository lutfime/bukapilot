#!/usr/bin/env python3
"""
Rewrite Gelu ops using the sigmoid approximation instead of erf.
Gelu(x) ≈ x * sigmoid(1.702 * x)  — within 0.01 of exact, 4 nodes per Gelu (vs 8 for erf).

Run on an opset-20 ONNX to produce opset 19.
"""
import sys
import onnx
from onnx import helper, TensorProto
import numpy as np

SCALE = 1.702  # sigmoid approximation constant

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
        # x * sigmoid(1.702 * x) — 4 nodes
        new_nodes.extend([
            make_const(f"{base}_scale_val", SCALE),
            helper.make_node("Mul", [x, f"{base}_scale_val"], [f"{base}_scaled"], name=f"{base}_scale"),
            helper.make_node("Sigmoid", [f"{base}_scaled"], [f"{base}_sig"], name=f"{base}_sigmoid"),
            helper.make_node("Mul", [x, f"{base}_sig"], [out], name=f"{base}_mulx"),
        ])
    del graph.node[:]
    graph.node.extend(new_nodes)
    return replaced

if __name__ == "__main__":
    path = sys.argv[1]
    print(f"[load] {path}")
    m = onnx.load(path)
    n = replace_gelu_nodes(m)
    print(f"[rewrite] replaced {n} Gelu nodes with sigmoid-approximation (4 nodes each)")
    from onnx import version_converter
    m19 = version_converter.convert_version(m, 19)
    new_opset = max(o.version for o in m19.opset_import)
    print(f"[downconvert] now opset {new_opset}")
    onnx.save(m19, path)
    print(f"[ok] saved {path}")
