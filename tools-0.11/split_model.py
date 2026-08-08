#!/usr/bin/env python3
"""
Split supercombo ONNX into vision and policy subgraphs using onnx-graphsurgeon.

Vision: img + big_img → hidden_state (512) + perception outputs
Policy: features_buffer + desire_pulse + traffic_convention + action_t + hidden_state → plan + desire_state + action
"""
import os, sys
import onnx
import onnx_graphsurgeon as gs
import numpy as np

REPO = os.path.abspath(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
INPUT = os.path.join(REPO, "selfdrive/modeld/models/driving_supercombo.onnx")
VISION_OUT = os.path.join(REPO, "selfdrive/modeld/models/driving_supercombo_vision.onnx")
POLICY_OUT = os.path.join(REPO, "selfdrive/modeld/models/driving_supercombo_policy.onnx")

@gs.Graph.register()
def identity(self, name, inp):
    return self.layer(name=name, op="Identity", inputs=[inp], outputs=[])

def main():
    print(f"Loading {INPUT}...")
    graph = gs.import_onnx(onnx.load(INPUT))
    print(f"Graph: {len(graph.nodes)} nodes, {len(graph.inputs)} inputs")

    # Find hidden_state tensor
    hidden_state = None
    for node in graph.nodes:
        if node.name == 'node_linear' and node.op == 'Gemm':
            hidden_state = node.outputs[0]
            print(f"Split point: {hidden_state.name} (from {node.name})")
            break

    if not hidden_state:
        print("ERROR: hidden_state not found")
        return

    # Also find the other perception output tensors we want from vision
    # These feed the final Concat (node_cat_2)
    cat_node = None
    for node in graph.nodes:
        if node.op == 'Concat' and len(node.inputs) >= 14:
            cat_node = node
            break

    # Perception outputs from Concat inputs [0-7, 9-10, 13] (everything except plan, action, desire_state)
    # [8] = plan (mul_18), [11] = desire_state (linear_55), [12] = action (mul_25)
    # We want vision to output: perception outputs + hidden_state
    # For simplicity, let's just output hidden_state from vision first
    # and keep ALL other outputs in the full graph for the policy part

    # === VISION MODEL ===
    print("\n=== Building vision model ===")
    vision_graph = gs.import_onnx(onnx.load(INPUT))

    # Find hidden_state in this copy
    vs_tensor = None
    for node in vision_graph.nodes:
        if node.name == 'node_linear' and node.op == 'Gemm':
            vs_tensor = node.outputs[0]
            break

    # Set vision output to just hidden_state
    # graphsurgeon will prune everything not needed
    vision_graph.outputs = [vs_tensor]

    # Also keep graph inputs that vision needs (img, big_img)
    # Remove unused inputs
    needed_inputs = {'img', 'big_img'}
    vision_graph.inputs = [inp for inp in vision_graph.inputs if inp.name in needed_inputs]

    # Cleanup to prune unused nodes
    vision_graph = vision_graph.cleanup().toposort()

    # Fix output shape
    for out in vision_graph.outputs:
        out.shape = [1, 512]

    vision_onnx = gs.export_onnx(vision_graph)
    onnx.save(vision_onnx, VISION_OUT)
    print(f"  Vision: {len(vision_graph.nodes)} nodes → {VISION_OUT}")
    print(f"  Inputs: {[i.name for i in vision_graph.inputs]}")
    print(f"  Outputs: {[o.name for o in vision_graph.outputs]}")

    # === POLICY MODEL ===
    print("\n=== Building policy model ===")
    policy_graph = gs.import_onnx(onnx.load(INPUT))

    # Find hidden_state in this copy
    ps_tensor = None
    for node in policy_graph.nodes:
        if node.name == 'node_linear' and node.op == 'Gemm':
            ps_tensor = node.outputs[0]
            break

    # Make hidden_state a graph INPUT (it comes from vision model)
    # Add it as a graph input
    hs_input = gs.Variable(name="hidden_state_input", dtype=np.float32, shape=[1, 512])

    # Replace all references to the hidden_state tensor with our new input
    # Find the Gemm node that produces hidden_state and bypass it
    for node in policy_graph.nodes:
        if node.name == 'node_linear':
            # This node's output is hidden_state
            # We want to replace it with our external input
            # Redirect: mark this node's output as our input
            ps_tensor.inputs = []  # detach
            break

    # Actually, the cleanest approach: use graphsurgeon to set hidden_state as input
    # and remove the vision nodes that produce it
    policy_graph.inputs.append(hs_input)

    # The policy model's output: we need to identify which outputs are "policy"
    # The final Concat output has slices:
    # plan [917:1907], desire_state [2054:2062], action [2062:2066]
    # We want to keep the full output for now and slice it externally
    # Actually let's keep all outputs like the original

    # Set outputs to the final Concat output
    final_output = policy_graph.outputs[0]
    policy_graph.outputs = [final_output]

    # Cleanup: remove nodes that produce hidden_state and are no longer needed
    # (the vision backbone)
    policy_graph = policy_graph.cleanup().toposort()

    policy_onnx = gs.export_onnx(policy_graph)
    onnx.save(policy_onnx, POLICY_OUT)
    print(f"  Policy: {len(policy_graph.nodes)} nodes → {POLICY_OUT}")
    print(f"  Inputs: {[i.name for i in policy_graph.inputs]}")
    print(f"  Outputs: {[o.name for o in policy_graph.outputs]}")

    print(f"\n=== Split complete ===")
    print(f"Vision: {VISION_OUT} ({len(vision_graph.nodes)} nodes)")
    print(f"Policy: {POLICY_OUT} ({len(policy_graph.nodes)} nodes)")
    print(f"Original was: {len(graph.nodes)} nodes")

if __name__ == "__main__":
    main()
