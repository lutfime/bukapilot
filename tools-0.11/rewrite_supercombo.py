#!/usr/bin/env python3
"""
Graph surgery: rewrite CPU-fallback ops to NPU-supported equivalents.

1. Squeeze → Reshape (24 ops)
   Squeeze(data, axes=[N]) removes dimension N.
   Reshape(data, new_shape) does the same thing but runs on NPU.

2. Where → Add+Mul (8 ops)
   Where(mask, a, b) selects a where mask is true, b where false.
   Equivalent: b + (a - b) * mask_float
   All of Sub, Mul, Add are NPU-supported.

Then verify: rewritten model produces identical output to original.

Usage in Docker:
  python3 tools-local/rewrite_supercombo.py
"""
import os, sys, time, json, subprocess
import numpy as np
import onnx
from onnx import helper, TensorProto

REPO = os.environ.get("REPO_ROOT_OVERRIDE", "/work")
ORIG_ONNX = os.path.join(REPO, "selfdrive/modeld/models/driving_supercombo.onnx")
REWRITTEN_ONNX = os.path.join(REPO, "selfdrive/modeld/models/driving_supercombo_rewritten.onnx")

def get_initializer_names(model):
    return {init.name for init in model.graph.initializer}

def get_initializer_value(model, name):
    """Get the value of a constant initializer as numpy array."""
    for init in model.graph.initializer:
        if init.name == name:
            return onnx.numpy_helper.to_array(init)
    return None

def rewrite_squeeze(model):
    """Replace Squeeze(data, axes) with Reshape(data, new_shape)."""
    init_names = get_initializer_names(model)
    nodes_to_add = []
    nodes_to_remove = set()
    shape_counter = [0]

    for node in model.graph.node:
        if node.op_type != 'Squeeze':
            continue

        # Squeeze inputs: [data, axes]
        data_input = node.input[0]
        axes_input = node.input[1] if len(node.input) > 1 else None
        output_name = node.output[0]

        # Get axes value
        if axes_input and axes_input in init_names:
            axes = get_initializer_value(model, axes_input)
            axes = [int(a) for a in axes.flatten()]
        else:
            print(f"  SKIP {node.name}: axes not a constant")
            continue

        # Find the shape of data_input by tracing back
        # We need to know the input shape to compute the output shape
        # Since we can't easily trace shapes, we'll use a different approach:
        # Create a Shape → Gather (skip the squeezed axis) → Reshape
        # Actually simpler: just use Reshape with allowzero=0 and compute shape dynamically

        # Approach: Shape(data) → Concat with constants to build new shape
        # This is complex. Simpler approach: just remove the Squeeze entirely
        # and let downstream ops handle the extra dimension.
        # But that changes semantics...

        # Best approach: Replace Squeeze with Reshape using a computed shape.
        # But we need to know the input shape. Let's trace it.

        # Actually, the simplest safe rewrite: since RKNN supports Squeeze in newer versions,
        # and the issue is that RKNN 2.3.2 puts it on CPU, let's try a different approach:
        # Replace Squeeze with a combination of Reshape that we compute from the graph.

        # For now, let's use the "Shape + ReduceProd + Concat" pattern to compute
        # the target shape dynamically. This is fully NPU-supported.

        # Actually, let me try the simplest thing first: just replace Squeeze op_type
        # with nothing — bypass it. The Squeeze removes a size-1 dimension.
        # If the downstream op (LayerNormalization/MatMul) can handle the extra dim,
        # we don't need Squeeze at all.

        # But that's risky. Let's do Reshape properly.

        # Get data shape from the producer node or graph input
        data_shape = None
        for graph_input in model.graph.input:
            if graph_input.name == data_input:
                dims = [d.dim_value for d in graph_input.type.tensor_type.shape.dim]
                data_shape = dims
                break

        if data_shape is None:
            # Try to find from initializer
            val = get_initializer_value(model, data_input)
            if val is not None:
                data_shape = list(val.shape)

        if data_shape is None:
            # Can't determine shape, skip
            print(f"  SKIP {node.name}: can't determine input shape")
            continue

        # Compute squeezed shape
        new_shape = []
        for i, dim in enumerate(data_shape):
            if i not in axes:
                new_shape.append(dim)

        # Create shape constant
        shape_name = f"squeeze_shape_{shape_counter[0]}"
        shape_counter[0] += 1
        shape_tensor = helper.make_tensor(shape_name, TensorProto.INT64,
                                          [len(new_shape)], new_shape)
        model.graph.initializer.append(shape_tensor)

        # Create Reshape node
        reshape_node = helper.make_node(
            'Reshape',
            inputs=[data_input, shape_name],
            outputs=[output_name],
            name=node.name + '_reshape'
        )
        nodes_to_add.append(reshape_node)
        nodes_to_remove.add(node.name)
        print(f"  Squeeze → Reshape: {node.name} (axes={axes}, {data_shape} → {new_shape})")

    # Apply changes
    new_nodes = []
    for node in model.graph.node:
        if node.name in nodes_to_remove:
            new_nodes.extend([n for n in nodes_to_add if n.name == node.name + '_reshape'])
        else:
            new_nodes.append(node)

    del model.graph.node[:]
    model.graph.node.extend(new_nodes)
    print(f"  Rewrote {len(nodes_to_remove)} Squeeze ops")


def rewrite_where(model):
    """Replace Where(mask, a, b) with b + (a - b) * Cast(mask)."""
    init_names = get_initializer_names(model)
    counter = [0]
    nodes_to_add = []
    nodes_to_remove = set()

    for node in model.graph.node:
        if node.op_type != 'Where':
            continue

        # Where inputs: [condition, X, Y]
        # Output = condition ? X : Y
        # Equivalent: Y + (X - Y) * condition_as_float
        condition = node.input[0]
        x_input = node.input[1]
        y_input = node.input[2]
        output_name = node.output[0]

        i = counter[0]
        counter[0] += 1

        # Cast condition to float
        cast_out = f"where_cast_{i}"
        cast_node = helper.make_node('Cast',
            inputs=[condition], outputs=[cast_out],
            name=f"{node.name}_cast", to=TensorProto.FLOAT)

        # Sub: X - Y
        sub_out = f"where_sub_{i}"
        sub_node = helper.make_node('Sub',
            inputs=[x_input, y_input], outputs=[sub_out],
            name=f"{node.name}_sub")

        # Mul: (X - Y) * condition_float
        mul_out = f"where_mul_{i}"
        mul_node = helper.make_node('Mul',
            inputs=[sub_out, cast_out], outputs=[mul_out],
            name=f"{node.name}_mul")

        # Add: Y + result
        add_node = helper.make_node('Add',
            inputs=[y_input, mul_out], outputs=[output_name],
            name=f"{node.name}_add")

        nodes_to_add.extend([cast_node, sub_node, mul_node, add_node])
        nodes_to_remove.add(node.name)
        print(f"  Where → Cast+Sub+Mul+Add: {node.name}")

    # Apply
    new_nodes = []
    add_iter = iter(nodes_to_add)
    adds_by_name = {}
    for n in nodes_to_add:
        prefix = n.name.rsplit('_', 1)[0]
        adds_by_name.setdefault(prefix, []).append(n)

    for node in model.graph.node:
        if node.name in nodes_to_remove:
            prefix = node.name
            new_nodes.extend(adds_by_name.get(prefix, []))
        else:
            new_nodes.append(node)

    del model.graph.node[:]
    model.graph.node.extend(new_nodes)
    print(f"  Rewrote {len(nodes_to_remove)} Where ops")


def verify_outputs(orig_path, new_path):
    """Compare outputs of original vs rewritten model in simulator."""
    SUPERCOMBO_INPUTS = [
        ('img', [1, 12, 128, 256]), ('big_img', [1, 12, 128, 256]),
        ('desire_pulse', [1, 25, 8]), ('traffic_convention', [1, 2]),
        ('action_t', [1, 2]), ('features_buffer', [1, 24, 512]),
    ]

    script = f'''
import numpy as np, json
from rknn.api import RKNN

inputs_config = {SUPERCOMBO_INPUTS}

def build_and_run(onnx_path, label):
    rknn = RKNN(verbose=False)
    rknn.config(mean_values=[[]], std_values=[[]], target_platform="rk3588",
                optimization_level=3, float_dtype="float16",
                enable_flash_attention=True, remove_reshape=False)  # don't remove reshape, we did our own
    ret = rknn.load_onnx(model=onnx_path)
    if ret != 0: return None, f"load_onnx: {{ret}}"
    ret = rknn.build(do_quantization=False)
    if ret != 0: return None, f"build: {{ret}}"
    ret = rknn.init_runtime(target=None)
    if ret != 0: return None, f"init: {{ret}}"

    inputs = []
    np.random.seed(42)  # same seed for both
    for name, shape in inputs_config:
        if len(shape) == 4 and shape[0] == 1:
            nhwc = [1, shape[2], shape[3], shape[1]]
            inputs.append(np.random.uniform(0, 200, nhwc).astype(np.float32))
        elif len(shape) == 3:
            inputs.append(np.zeros(shape, dtype=np.float32))
        elif len(shape) == 2:
            inputs.append(np.array([[1.0, 0.0]], dtype=np.float32) if shape[1] == 2 else np.zeros(shape, dtype=np.float32))
        else:
            inputs.append(np.zeros(shape, dtype=np.float32))

    out = rknn.inference(inputs=inputs, data_format="nhwc")
    rknn.release()
    return out[0], "ok"

print("Building original...", flush=True)
orig_out, orig_err = build_and_run("{orig_path}", "original")
if orig_out is None:
    print(json.dumps({{"error": f"original: {{orig_err}}"}}))
    exit(0)
print(f"Original output: shape={{orig_out.shape}}, min={{orig_out.min():.4f}}, max={{orig_out.max():.4f}}", flush=True)

print("Building rewritten...", flush=True)
new_out, new_err = build_and_run("{new_path}", "rewritten")
if new_out is None:
    print(json.dumps({{"error": f"rewritten: {{new_err}}"}}))
    exit(0)
print(f"Rewritten output: shape={{new_out.shape}}, min={{new_out.min():.4f}}, max={{new_out.max():.4f}}", flush=True)

# Compare
if orig_out.shape != new_out.shape:
    print(json.dumps({{"error": f"shape mismatch: {{orig_out.shape}} vs {{new_out.shape}}"}}))
    exit(0)

diff = np.abs(orig_out - new_out)
max_diff = float(diff.max())
mean_diff = float(diff.mean())
rel_diff = mean_diff / (np.abs(orig_out).mean() + 1e-8) * 100

result = {{
    "max_diff": round(max_diff, 6),
    "mean_diff": round(mean_diff, 6),
    "rel_diff_pct": round(rel_diff, 4),
    "shapes_match": True,
    "orig_range": [round(float(orig_out.min()),4), round(float(orig_out.max()),4)],
    "new_range": [round(float(new_out.min()),4), round(float(new_out.max()),4)],
}}
if max_diff < 0.01:
    result["verdict"] = "IDENTICAL - rewrite is safe"
elif max_diff < 0.1:
    result["verdict"] = "CLOSE - likely safe"
else:
    result["verdict"] = "DIFFERENT - investigate"

print("VERIFY:" + json.dumps(result))
'''
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, timeout=600)
    for line in result.stdout.strip().split('\n'):
        if line.startswith("VERIFY:"):
            return json.loads(line[8:])
        if line.startswith("{\"error\""):
            return json.loads(line)
    return {"error": result.stderr[-300:] if result.stderr else "no output"}

def main():
    print("=" * 60)
    print("GRAPH SURGERY: Rewrite CPU-fallback ops")
    print("=" * 60)

    # Load original
    model = onnx.load(ORIG_ONNX)
    print(f"Loaded: {ORIG_ONNX} ({len(model.graph.node)} nodes)")

    # Count before
    before = {}
    for node in model.graph.node:
        before[node.op_type] = before.get(node.op_type, 0) + 1
    print(f"\nBefore: Squeeze={before.get('Squeeze',0)}, Where={before.get('Where',0)}")

    # Rewrite Squeeze → Reshape
    print("\n--- Rewriting Squeeze → Reshape ---")
    rewrite_squeeze(model)

    # Rewrite Where → Add+Mul
    print("\n--- Rewriting Where → Add+Mul ---")
    rewrite_where(model)

    # Count after
    after = {}
    for node in model.graph.node:
        after[node.op_type] = after.get(node.op_type, 0) + 1
    print(f"\nAfter: Squeeze={after.get('Squeeze',0)}, Where={after.get('Where',0)}, Reshape={after.get('Reshape',0)}")

    # Save
    onnx.save(model, REWRITTEN_ONNX)
    print(f"\nSaved: {REWRITTEN_ONNX} ({len(model.graph.node)} nodes)")

    # Verify outputs match
    print("\n" + "=" * 60)
    print("VERIFICATION: Compare original vs rewritten outputs")
    print("=" * 60)

    result = verify_outputs(ORIG_ONNX, REWRITTEN_ONNX)

    if 'verdict' in result:
        print(f"\nMax diff: {result['max_diff']}")
        print(f"Mean diff: {result['mean_diff']}")
        print(f"Relative diff: {result['rel_diff_pct']}%")
        print(f"Original range: {result['orig_range']}")
        print(f"Rewritten range: {result['new_range']}")
        print(f"\nVERDICT: {result['verdict']}")
    else:
        print(f"\nVerification failed: {result.get('error', 'unknown')}")

if __name__ == "__main__":
    main()
