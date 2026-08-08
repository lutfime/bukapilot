#!/usr/bin/env python3
"""
Complete graph surgery:
1. Fix float16/float32 type mismatches (from Gelu rewrite)
2. Rewrite Squeeze → Reshape (using ONNX shape inference for shapes)
3. Rewrite Where → Cast+Sub+Mul+Add
4. Verify with onnxruntime

Usage in Docker:
  python3 tools-local/rewrite_v2.py
"""
import os, sys, numpy as np
import onnx
from onnx import helper, TensorProto, shape_inference

REPO = os.environ.get("REPO_ROOT_OVERRIDE", "/work")
ORIG = os.path.join(REPO, "selfdrive/modeld/models/driving_supercombo.onnx")
OUTPUT = os.path.join(REPO, "selfdrive/modeld/models/driving_supercombo_rewritten.onnx")

def get_value_info(model, name):
    """Get shape/dtype from value_info, graph inputs, or initializers."""
    # Check graph inputs
    for vi in model.graph.input:
        if vi.name == name:
            return vi.type.tensor_type
    # Check graph outputs
    for vi in model.graph.output:
        if vi.name == name:
            return vi.type.tensor_type
    # Check value_info (from shape inference)
    for vi in model.graph.value_info:
        if vi.name == name:
            return vi.type.tensor_type
    return None

def get_shape(model, name):
    """Get shape as list of ints from value_info."""
    tt = get_value_info(model, name)
    if tt is None:
        return None
    dims = []
    for d in tt.shape.dim:
        if d.HasField('dim_value'):
            dims.append(d.dim_value)
        else:
            dims.append(-1)  # unknown
    return dims if dims else None

def get_initializer(model, name):
    for init in model.graph.initializer:
        if init.name == name:
            return onnx.numpy_helper.to_array(init)
    return None

def fix_type_mismatches(model):
    """Fix float16/float32 mismatches by adding Cast nodes."""
    fixed = 0
    nodes_to_add = []
    nodes_to_remove = set()
    cast_counter = [0]

    for node in model.graph.node:
        if node.op_type in ('Mul', 'Add', 'Sub', 'Div'):
            # Check input types
            input_types = []
            for inp in node.input:
                tt = get_value_info(model, inp)
                if tt:
                    input_types.append(tt.elem_type)
                else:
                    val = get_initializer(model, inp)
                    if val is not None:
                        input_types.append(TensorProto.FLOAT if val.dtype == np.float32 else TensorProto.FLOAT16)
                    else:
                        input_types.append(0)  # unknown

            # If inputs have mixed float/float16, cast all to float32
            if TensorProto.FLOAT16 in input_types and TensorProto.FLOAT in input_types:
                new_inputs = []
                for i, (inp, dt) in enumerate(zip(node.input, input_types)):
                    if dt == TensorProto.FLOAT16:
                        # Add Cast float16 → float32
                        cast_name = f"type_fix_cast_{cast_counter[0]}"
                        cast_counter[0] += 1
                        cast_out = f"type_fix_{cast_counter[0]}"
                        cast_counter[0] += 1
                        cast_node = helper.make_node('Cast',
                            inputs=[inp], outputs=[cast_out],
                            name=cast_name, to=TensorProto.FLOAT)
                        nodes_to_add.append((node.name, cast_node))
                        new_inputs.append(cast_out)
                    else:
                        new_inputs.append(inp)
                # Update node inputs
                del node.input[:]
                node.input.extend(new_inputs)
                fixed += 1

    # Insert cast nodes before their parent nodes
    if nodes_to_add:
        new_nodes = []
        for node in model.graph.node:
            for parent_name, cast_node in nodes_to_add:
                if node.name == parent_name:
                    new_nodes.append(cast_node)
            new_nodes.append(node)
        del model.graph.node[:]
        model.graph.node.extend(new_nodes)

    return fixed

def rewrite_squeeze(model):
    """Replace Squeeze with Reshape using shape inference."""
    counter = [0]
    replaced = 0
    nodes_to_add = []
    nodes_to_remove = set()

    for node in model.graph.node:
        if node.op_type != 'Squeeze':
            continue

        data_input = node.input[0]
        output_name = node.output[0]

        # Get axes
        axes_input = node.input[1] if len(node.input) > 1 else None
        if axes_input:
            axes_val = get_initializer(model, axes_input)
            if axes_val is None:
                continue
            axes = [int(a) for a in axes_val.flatten()]
        else:
            axes = None  # squeeze all size-1 dims

        # Get input shape from shape inference
        data_shape = get_shape(model, data_input)
        if data_shape is None:
            continue

        # Handle negative axes
        axes = [a if a >= 0 else a + len(data_shape) for a in axes]

        # Compute output shape
        if axes:
            new_shape = [d for i, d in enumerate(data_shape) if i not in axes]
        else:
            new_shape = [d for d in data_shape if d != 1]

        # Replace -1 (unknown dims) - use the known dims, replace unknown with -1
        # Reshape allows one -1 (inferred dimension)
        # But we need explicit shape for RKNN. Use the actual values if known.
        if any(d == -1 for d in new_shape):
            # Can't compute exact shape, skip
            continue

        # Create shape constant
        shape_name = f"sq_shape_{counter[0]}"
        counter[0] += 1
        shape_tensor = helper.make_tensor(shape_name, TensorProto.INT64,
                                          [len(new_shape)], new_shape)
        model.graph.initializer.append(shape_tensor)

        reshape_node = helper.make_node('Reshape',
            inputs=[data_input, shape_name],
            outputs=[output_name],
            name=node.name + '_rs')

        nodes_to_add.append((node.name, reshape_node))
        nodes_to_remove.add(node.name)
        replaced += 1
        print(f"  Squeeze→Reshape: {node.name} axes={axes} {data_shape}→{new_shape}")

    # Apply
    new_nodes = []
    for node in model.graph.node:
        if node.name in nodes_to_remove:
            for parent_name, new_node in nodes_to_add:
                if parent_name == node.name:
                    new_nodes.append(new_node)
        else:
            new_nodes.append(node)
    del model.graph.node[:]
    model.graph.node.extend(new_nodes)

    return replaced

def rewrite_where(model):
    """Replace Where(cond, X, Y) with Y + (X - Y) * Cast(cond)."""
    counter = [0]
    replaced = 0
    nodes_to_add = []
    nodes_to_remove = set()

    for node in model.graph.node:
        if node.op_type != 'Where':
            continue

        condition = node.input[0]
        x_input = node.input[1]
        y_input = node.input[2]
        output_name = node.output[0]
        i = counter[0]
        counter[0] += 1

        cast_out = f"wh_cast_{i}"
        sub_out = f"wh_sub_{i}"
        mul_out = f"wh_mul_{i}"

        cast_node = helper.make_node('Cast', inputs=[condition], outputs=[cast_out],
                                      name=f"{node.name}_c", to=TensorProto.FLOAT)
        sub_node = helper.make_node('Sub', inputs=[x_input, y_input], outputs=[sub_out],
                                     name=f"{node.name}_s")
        mul_node = helper.make_node('Mul', inputs=[sub_out, cast_out], outputs=[mul_out],
                                     name=f"{node.name}_m")
        add_node = helper.make_node('Add', inputs=[y_input, mul_out], outputs=[output_name],
                                     name=f"{node.name}_a")

        nodes_to_add.append((node.name, [cast_node, sub_node, mul_node, add_node]))
        nodes_to_remove.add(node.name)
        replaced += 1
        print(f"  Where→Cast+Sub+Mul+Add: {node.name}")

    # Apply
    new_nodes = []
    for node in model.graph.node:
        if node.name in nodes_to_remove:
            for parent_name, new_nodes_list in nodes_to_add:
                if parent_name == node.name:
                    new_nodes.extend(new_nodes_list)
        else:
            new_nodes.append(node)
    del model.graph.node[:]
    model.graph.node.extend(new_nodes)

    return replaced

def main():
    print("=" * 60)
    print("GRAPH SURGERY v2")
    print("=" * 60)

    model = onnx.load(ORIG)
    print(f"Loaded: {len(model.graph.node)} nodes")

    # Step 0: Run shape inference to get intermediate shapes
    print("\n[0] Running shape inference...")
    model = shape_inference.infer_shapes(model)
    print(f"  Shape inference done")

    # Step 1: Fix type mismatches
    print("\n[1] Fixing float16/float32 type mismatches...")
    fixed = fix_type_mismatches(model)
    print(f"  Fixed {fixed} type mismatches")

    # Re-run shape inference after fixes
    model = shape_inference.infer_shapes(model)

    # Step 2: Rewrite Squeeze → Reshape
    print("\n[2] Rewriting Squeeze → Reshape...")
    sq_count = rewrite_squeeze(model)
    print(f"  Rewrote {sq_count} Squeeze ops")

    # Step 3: Rewrite Where → Cast+Sub+Mul+Add
    print("\n[3] Rewriting Where → Cast+Sub+Mul+Add...")
    wh_count = rewrite_where(model)
    print(f"  Rewrote {wh_count} Where ops")

    # Count remaining fallback ops
    remaining = {}
    for node in model.graph.node:
        if node.op_type in ('Squeeze', 'Unsqueeze', 'Where', 'GatherND', 'Gather'):
            remaining[node.op_type] = remaining.get(node.op_type, 0) + 1
    print(f"\nRemaining fallback ops: {remaining}")
    print(f"Total nodes: {len(model.graph.node)}")

    # Save
    onnx.save(model, OUTPUT)
    print(f"\nSaved: {OUTPUT}")

    # Verify with onnxruntime
    print("\n" + "=" * 60)
    print("VERIFICATION (onnxruntime)")
    print("=" * 60)

    import onnxruntime as ort

    np.random.seed(42)
    orig_sess = ort.InferenceSession(ORIG, providers=["CPUExecutionProvider"])
    new_sess = ort.InferenceSession(OUTPUT, providers=["CPUExecutionProvider"])

    inputs = {}
    for inp in orig_sess.get_inputs():
        shape = [s if isinstance(s, int) and s > 0 else 1 for s in inp.shape]
        if len(shape) == 4:
            shape = [1, shape[1], shape[2], shape[3]]
        if inp.name in ("img", "big_img"):
            inputs[inp.name] = np.random.uniform(0, 200, shape).astype(np.float32)
        elif inp.name == "traffic_convention":
            inputs[inp.name] = np.array([[1.0, 0.0]], dtype=np.float32)
        else:
            inputs[inp.name] = np.zeros(shape, dtype=np.float32)

    orig_out = orig_sess.run(None, inputs)[0]
    new_out = new_sess.run(None, inputs)[0]

    print(f"Original:  shape={orig_out.shape} min={orig_out.min():.4f} max={orig_out.max():.4f}")
    print(f"Rewritten: shape={new_out.shape} min={new_out.min():.4f} max={new_out.max():.4f}")

    if orig_out.shape != new_out.shape:
        print("ERROR: Shape mismatch!")
        return

    diff = np.abs(orig_out - new_out)
    print(f"Max diff:  {diff.max():.6f}")
    print(f"Mean diff: {diff.mean():.6f}")
    rel = diff.mean() / (np.abs(orig_out).mean() + 1e-8) * 100
    print(f"Relative:  {rel:.4f}%")

    if diff.max() < 0.001:
        print("\nVERDICT: ✅ IDENTICAL - rewrite is safe!")
    elif diff.max() < 0.01:
        print("\nVERDICT: ⚠️ CLOSE - likely safe")
    else:
        print(f"\nVERDICT: ❌ DIFFERENT - max diff {diff.max():.4f}, investigate!")

if __name__ == "__main__":
    main()
