#!/usr/bin/env python3
"""Scale MatMul/Gemm weights by 1/2^k + insert Mul(2^k) after — fixes transient
fp16 accumulation overflow on RK3588 NPU.

Mechanism: the NPU's fp16 accumulator overflows MID-DOT-PRODUCT (partial sums
exceed 65504 before cancellation brings the final value down). Dividing weights
by 2^k shrinks every partial sum by 2^k; the Mul(2^k) after the node restores
the exact value AFTER accumulation completes.

Power-of-2 scaling is BIT-EXACT in fp16 (exponent shift only, no mantissa
rounding). Math is unchanged; only transient accumulation magnitude changes.

Only touches MatMul/Gemm nodes with a constant (initializer) weight input.
Dynamic matmuls (attention Q@K^T) are left alone (small scores, lower risk).

Usage: python3 scale_matmul_weights.py model.onnx [shift_bits=3]
"""
import sys
import numpy as np
import onnx
from onnx import helper, TensorProto, numpy_helper

def scale_model(path, shift=3):
    S = float(2 ** shift)
    m = onnx.load(path)
    graph = m.graph
    
    # Index initializers by name
    inits = {i.name: i for i in graph.initializer}
    
    # Also collect Constant node outputs (weights may be Constant nodes)
    const_nodes = {}
    for node in graph.node:
        if node.op_type == "Constant":
            for out in node.output:
                const_nodes[out] = node
    
    new_nodes = []
    scaled = 0
    mul_added = 0
    
    for node in graph.node:
        new_nodes.append(node)
        if node.op_type not in ("MatMul", "Gemm"):
            continue
        
        # Find a constant input (the weight). Prefer input[1] (typical y = x @ W),
        # fall back to input[0].
        weight_name = None
        for cand in (node.input[1] if len(node.input) > 1 else None, node.input[0] if node.input else None):
            if cand and (cand in inits or cand in const_nodes):
                weight_name = cand
                break
        if weight_name is None:
            continue  # both dynamic (attention) — skip
        
        # Scale the weight initializer by 1/S
        if weight_name in inits:
            init = inits[weight_name]
            arr = numpy_helper.to_array(init).astype(np.float32)
            if arr.max() == 0:
                continue
            arr = (arr / S).astype(np.float16 if init.data_type == TensorProto.FLOAT16 else np.float32)
            new_init = numpy_helper.from_array(arr, init.name)
            # Replace in graph
            for i, existing in enumerate(graph.initializer):
                if existing.name == init.name:
                    graph.initializer[i].CopyFrom(new_init)
                    break
        else:
            # Constant node — modify its value attribute
            cnode = const_nodes[weight_name]
            for attr in cnode.attribute:
                if attr.name == "value":
                    arr = numpy_helper.to_array(attr.t).astype(np.float32)
                    if arr.max() == 0:
                        continue
                    was_fp16 = attr.t.data_type == TensorProto.FLOAT16
                    arr = (arr / S).astype(np.float16 if was_fp16 else np.float32)
                    attr.t.CopyFrom(numpy_helper.from_array(arr, attr.t.name if attr.t.name else "c"))
        
        scaled += 1

        # Gemm bias: Gemm(A, W, B) = A@W + B. With W→W/S and output×S, the bias
        # would become S*B — WRONG. Scale the bias by 1/S as well:
        # (A@(W/S) + B/S) × S = A@W + B. Correct.
        if node.op_type == "Gemm" and len(node.input) > 2 and node.input[2]:
            bias_name = node.input[2]
            if bias_name in inits:
                binit = inits[bias_name]
                barr = numpy_helper.to_array(binit).astype(np.float32)
                if barr.size > 0 and np.abs(barr).max() != 0:
                    barr = (barr / S).astype(np.float16 if binit.data_type == TensorProto.FLOAT16 else np.float32)
                    new_binit = numpy_helper.from_array(barr, binit.name)
                    for i, existing in enumerate(graph.initializer):
                        if existing.name == binit.name:
                            graph.initializer[i].CopyFrom(new_binit)
                            break
            elif bias_name in const_nodes:
                for attr in const_nodes[bias_name].attribute:
                    if attr.name == "value":
                        barr = numpy_helper.to_array(attr.t).astype(np.float32)
                        if barr.size > 0 and np.abs(barr).max() != 0:
                            was_fp16 = attr.t.data_type == TensorProto.FLOAT16
                            barr = (barr / S).astype(np.float16 if was_fp16 else np.float32)
                            attr.t.CopyFrom(numpy_helper.from_array(barr, attr.t.name if attr.t.name else "b"))

        # Insert Mul(S) after this node's output
        for k, out_name in enumerate(node.output):
            if not out_name:
                continue
            new_out = out_name + "_unscaled"
            scale_const = helper.make_tensor(node.name + "_scale_c", TensorProto.FLOAT16, [], [np.float16(S)])
            scale_init = numpy_helper.from_array(np.array([S], dtype=np.float16), node.name + "_scale_w")
            graph.initializer.append(scale_init)
            mul_node = helper.make_node(
                "Mul",
                [new_out, node.name + "_scale_w"],
                [out_name],
                name=node.name + "_unscale",
            )
            # Rename the matmul's output; consumers now read from the Mul
            node.output[k] = new_out
            new_nodes.append(mul_node)
            mul_added += 1
            break  # only first output
    
    del graph.node[:]
    graph.node.extend(new_nodes)
    
    onnx.save(m, path)
    print("[scale] %s: scaled %d weight tensors, added %d un-scale Muls (S=%g)" % (path, scaled, mul_added, S))
    return scaled

if __name__ == "__main__":
    path = sys.argv[1]
    shift = int(sys.argv[2]) if len(sys.argv) > 2 else 3
    scale_model(path, shift)
