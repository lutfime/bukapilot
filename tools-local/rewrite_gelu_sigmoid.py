#!/usr/bin/env python3
"""
Rewrite Gelu ops using the sigmoid approximation with fp16 overflow protection.
Gelu(x) ≈ x * sigmoid(1.702 * clip(x, -CLIP_MAX, CLIP_MAX))

The Clip prevents fp16 overflow: without it, 1.702 * x can exceed fp16 max (65504)
when x > ~38500, producing inf in intermediate results. This is the suspected root
cause of the C API inf bug (librknnrt v2.3.2 C API doesn't handle fp16 inf propagation
the same way Python rknnlite does).

Clipping at ±100 has ZERO accuracy impact: sigmoid(1.702 * 100) = sigmoid(170.2) = 1.0,
so Gelu(100) = 100 * 1.0 = 100 — identical to the unclipped result. The clip only
activates for extreme values that would overflow fp16 anyway.

5 nodes per Gelu (vs 4 without clip, vs 8 for erf).

Run on an opset-20 ONNX to produce opset 19.
"""
import sys
import onnx
from onnx import helper, TensorProto
import numpy as np

SCALE = 1.702    # sigmoid approximation constant
CLIP_MAX = 100.0 # clamp x before multiply; prevents 1.702*x from overflowing fp16

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
        # x_clipped = clip(x, -CLIP_MAX, CLIP_MAX)
        # sigmoid_out = sigmoid(SCALE * x_clipped)
        # result = x * sigmoid_out
        #
        # NOTE: the final Mul uses ORIGINAL x (not x_clipped), so for large x
        # the output is x * 1.0 = x (correct Gelu asymptote). The clip only
        # protects the intermediate SCALE*x from overflowing fp16.
        new_nodes.extend([
            # Clip x to prevent overflow in the scale multiply
            make_const(f"{base}_clip_min", -CLIP_MAX),
            make_const(f"{base}_clip_max", CLIP_MAX),
            helper.make_node("Clip",
                [x, f"{base}_clip_min", f"{base}_clip_max"],
                [f"{base}_clipped"], name=f"{base}_clip"),
            # Scale: SCALE * x_clipped (now guaranteed fp16-safe)
            make_const(f"{base}_scale_val", SCALE),
            helper.make_node("Mul", [f"{base}_clipped", f"{base}_scale_val"],
                [f"{base}_scaled"], name=f"{base}_scale"),
            # Sigmoid
            helper.make_node("Sigmoid", [f"{base}_scaled"], [f"{base}_sig"],
                name=f"{base}_sigmoid"),
            # Final multiply with ORIGINAL x (not clipped)
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
    print(f"[rewrite] replaced {n} Gelu nodes with clipped-sigmoid (5 nodes each, fp16-safe)")
    from onnx import version_converter
    m19 = version_converter.convert_version(m, 19)
    new_opset = max(o.version for o in m19.opset_import)
    print(f"[downconvert] now opset {new_opset}")
    onnx.save(m19, path)
    print(f"[ok] saved {path}")
