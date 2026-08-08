#!/usr/bin/env python3
"""
Split the 0.11 driving_supercombo.onnx into vision and policy parts.

Vision part: img + big_img + features_buffer → perception outputs + hidden_state (512)
Policy part: hidden_state + desire_pulse + traffic_convention → plan + action + desire_state

The split point is after node_linear (vision encoder head, produces 512-dim hidden_state).
Everything that produces the perception outputs and hidden_state goes in vision.
Everything that consumes hidden_state to produce plan/action goes in policy.

Note: This is complex because the supercombo has auto-regressive features_buffer
that bridges vision and policy. The split needs to handle this carefully.

Usage in Docker:
  python3 tools-local/split_supercombo.py
"""
import os, sys
import onnx
from onnx import helper, TensorProto
from collections import defaultdict

REPO = os.environ.get("REPO_ROOT_OVERRIDE", "/work")
INPUT = os.path.join(REPO, "selfdrive/modeld/models/driving_supercombo.onnx")
VISION_OUT = os.path.join(REPO, "selfdrive/modeld/models/driving_supercombo_vision.onnx")
POLICY_OUT = os.path.join(REPO, "selfdrive/modeld/models/driving_supercombo_policy.onnx")

def main():
    m = onnx.load(INPUT)
    print(f"Loaded: {INPUT} ({len(m.graph.node)} nodes)")

    # Build dependency graph
    producers = {}
    consumers = defaultdict(list)
    for node in m.graph.node:
        for out in node.output:
            producers[out] = node
        for inp in node.input:
            consumers[inp].append(node)

    # The final Concat (node_cat_2) assembles 15 inputs into the 2580-element output
    # Input [13] = "linear" from node_linear = hidden_state (512 elements)
    # Input [14] = "pad" (the zero-size one we fixed, 1 element)

    # Strategy: Instead of splitting the graph (very complex), let's understand
    # what the 0.10 modeld runner already does.
    #
    # The 0.10 split works like this:
    #   1. Run vision model → get 1576 outputs (includes 512 hidden_state)
    #   2. Feed hidden_state into features_buffer of policy model
    #   3. Run policy model → get 1000 outputs (plan + desire_state)
    #
    # The 0.11 supercombo fuses this. But the INTERNAL structure is:
    #   vision backbone (Conv layers) → features → hidden_state (512)
    #   features_buffer (recurrent) + hidden_state → attention → policy heads
    #
    # The key insight: features_buffer is BOTH input AND output of the supercombo.
    #   Input: features_buffer [1,24,512] (from previous frame's hidden_state)
    #   Output: hidden_state [2066:2578] (becomes next frame's features_buffer)
    #
    # This auto-regressive loop is INSIDE the model. To split it, we'd need to
    # break this loop and manage it externally — like the 0.10 model did.

    # Let's find ALL the output slices and categorize them as "vision" or "policy"
    import pickle, base64
    slices = None
    for prop in m.metadata_props:
        if prop.key == 'output_slices':
            slices = pickle.loads(base64.b64decode(prop.value))

    # Categorize outputs
    # Vision outputs (perception): lane_lines, lane_lines_prob, road_edges,
    #   meta, desire_pred, pose, wide_from_device_euler, road_transform, lead, lead_prob
    # Policy outputs: plan, desire_state, action
    # Bridge: hidden_state (recurrent)

    vision_outputs = ['lane_lines', 'lane_lines_prob', 'road_edges', 'meta',
                      'desire_pred', 'pose', 'wide_from_device_euler', 'road_transform',
                      'lead', 'lead_prob', 'hidden_state']
    policy_outputs = ['plan', 'desire_state', 'action']

    print("\n=== Output categorization ===")
    print("Vision outputs (perception + hidden_state):")
    for name in vision_outputs:
        if name in slices:
            sl = slices[name]
            print(f"  {name}: [{sl.start}:{sl.stop}] = {sl.stop-sl.start} elements")

    print("\nPolicy outputs:")
    for name in policy_outputs:
        if name in slices:
            sl = slices[name]
            print(f"  {name}: [{sl.start}:{sl.stop}] = {sl.stop-sl.start} elements")

    # Now trace which nodes produce which Concat inputs
    cat_node = None
    for node in m.graph.node:
        if node.op_type == 'Concat' and len(node.input) >= 14:
            cat_node = node
            break

    # Map Concat inputs to output categories
    # The order in the Concat determines the slice positions
    concat_inputs = list(cat_node.input)
    print(f"\n=== Concat input → output mapping ===")

    # Calculate cumulative sizes
    pos = 0
    input_to_slice = {}
    for i, inp in enumerate(concat_inputs):
        # Find the producer to estimate size
        prod = producers.get(inp)
        if prod and prod.op_type == 'Gemm':
            # Check weight for output size
            for init in m.graph.initializer:
                if init.name == prod.input[1]:
                    size = init.dims[0]
                    input_to_slice[i] = (inp, pos, pos+size, size)
                    print(f"  [{i:2d}] {inp:40s} [{pos:4d}:{pos+size:4d}] = {size:4d}")
                    pos += size
                    break
        elif prod and prod.op_type == 'Mul':
            # Find the Mul's input size
            # Mul preserves shape, check first input
            mul_in = prod.input[0]
            mul_prod = producers.get(mul_in)
            if mul_prod and mul_prod.op_type == 'Gemm':
                for init in m.graph.initializer:
                    if init.name == mul_prod.input[1]:
                        size = init.dims[0]
                        input_to_slice[i] = (inp, pos, pos+size, size)
                        print(f"  [{i:2d}] {inp:40s} [{pos:4d}:{pos+size:4d}] = {size:4d}")
                        pos += size
                        break
        elif inp == 'pad':
            input_to_slice[i] = (inp, pos, pos+2, 2)
            print(f"  [{i:2d}] {inp:40s} [{pos:4d}:{pos+2:4d}] = {2:4d} (pad)")
            pos += 2

    total = pos
    print(f"\n  Total calculated: {total}")

    # Now identify the split: everything up to and including hidden_state is "vision part"
    # Everything after (plan, action, desire_state) is "policy part"
    # hidden_state is at Concat input [13] = node_linear output "linear"

    # The split approach:
    # We can't easily split the ONNX graph (too many cross-dependencies).
    # Instead, let's check if we can just run the supercombo model with
    # features_buffer=0 (no recurrent state) and see if the timing changes.
    # If the recurrent attention is what's slow, zeroing it out should be faster.

    # Actually, a simpler approach: just measure what percentage of compute
    # is in the vision backbone vs policy heads
    # The vision backbone has 60 Conv layers + the FastViT stages
    # The policy part has the attention + plan heads

    # Count Conv vs MatMul (attention) nodes
    conv_count = sum(1 for n in m.graph.node if n.op_type == 'Conv')
    matmul_count = sum(1 for n in m.graph.node if n.op_type == 'MatMul')
    gemm_count = sum(1 for n in m.graph.node if n.op_type == 'Gemm')

    print(f"\n=== Compute breakdown ===")
    print(f"Conv (vision backbone): {conv_count}")
    print(f"MatMul (attention): {matmul_count}")
    print(f"Gemm (linear heads): {gemm_count}")

    # Find where the first MatMul (attention) appears
    for i, node in enumerate(m.graph.node):
        if node.op_type == 'MatMul':
            print(f"\nFirst MatMul at node index {i} of {len(m.graph.node)}")
            print(f"  {node.name}: {list(node.input)} → {list(node.output)}")
            # What produces its inputs?
            for inp in node.input:
                prod = producers.get(inp)
                if prod:
                    print(f"  input {inp} ← {prod.op_type} {prod.name}")
            break

    print(f"\n=== Conclusion ===")
    print("The supercombo has the vision backbone (Conv layers) producing features,")
    print("then attention layers (MatMul) that mix features with the recurrent state,")
    print("then policy heads (Gemm) that output plan/action.")
    print()
    print("Splitting is architecturally possible but requires:")
    print("1. Tracing ALL dependencies from vision inputs to the hidden_state output")
    print("2. Tracing ALL dependencies from hidden_state to the policy outputs")
    print("3. Extracting two subgraphs")
    print("4. Writing a custom runner that pipelines them")
    print()
    print("This is a significant engineering effort (~1-2 days).")
    print("The onnx-simplifier or onnx-graphsurgeon tools could help automate the extraction.")

if __name__ == "__main__":
    main()
