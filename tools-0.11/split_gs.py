#!/usr/bin/env python3
"""
Split supercombo using onnx-graphsurgeon.

Vision model: img, big_img, features_buffer → perception outputs + hidden_state (512)
Policy model: hidden_state + desire_pulse + traffic_convention + action_t → plan, desire_state, action

The split point is node_linear (Gemm producing hidden_state "linear").
Everything that only depends on vision inputs = vision model.
Everything that depends on hidden_state = policy model.
"""
import os, sys, numpy as np
import onnx
import onnx_graphsurgeon as gs

REPO = os.environ.get("REPO_ROOT_OVERRIDE", os.getcwd())
if not os.path.isabs(REPO):
    REPO = os.path.abspath(REPO)
INPUT = os.path.join(REPO, "selfdrive/modeld/models/driving_supercombo.onnx")

def main():
    print(f"Loading {INPUT}...")
    graph = gs.import_onnx(onnx.load(INPUT))
    print(f"Graph: {len(graph.nodes)} nodes, {len(graph.inputs)} inputs, {len(graph.outputs)} outputs")

    # Print graph inputs/outputs
    print("\n=== Graph inputs ===")
    for inp in graph.inputs:
        print(f"  {inp.name}: shape={inp.shape} dtype={inp.dtype}")

    print("\n=== Graph outputs ===")
    for out in graph.outputs:
        print(f"  {out.name}: shape={out.shape} dtype={out.dtype}")

    # Find the hidden_state tensor ("linear" from node_linear)
    hidden_state_tensor = None
    for node in graph.nodes:
        if node.name == 'node_linear' and node.op == 'Gemm':
            hidden_state_tensor = node.outputs[0]
            print(f"\nHidden state tensor: {hidden_state_tensor.name}")
            break

    if not hidden_state_tensor:
        print("ERROR: Could not find hidden_state tensor")
        return

    # The vision model outputs: everything the final Concat assembles EXCEPT plan/action/desire_state
    # Plus the hidden_state tensor
    #
    # But actually, the simplest approach: make TWO copies of the graph
    # Vision graph: same inputs, output = hidden_state tensor (+ optionally perception outputs)
    # Policy graph: inputs = hidden_state + desire_pulse + traffic_convention + action_t, output = plan + action + desire_state

    # Step 1: Vision model — extract the subgraph that produces hidden_state
    print("\n=== Extracting vision subgraph ===")
    vision_graph = graph.fold_constants().cleanup()
    # Set the output to just hidden_state
    # This will prune everything not needed to produce it
    vision_graph.outputs = [hidden_state_tensor]
    # But we also want perception outputs — let's just do hidden_state first
    # to test the concept, then add perception later

    # Actually, gs.cleanup() with specific outputs will prune the graph
    # Let's try a different approach: just partition the node list

    # Find all nodes that are ancestors of hidden_state (vision backbone)
    def find_ancestors(tensor, all_nodes, producers):
        """Find all nodes that contribute to producing this tensor."""
        ancestors = set()
        queue = [tensor]
        visited = set()
        while queue:
            t = queue.pop(0)
            if t.name in visited:
                continue
            visited.add(t.name)
            prod = producers.get(t.name)
            if prod:
                ancestors.add(prod)
                for inp in prod.inputs:
                    queue.append(inp)
        return ancestors

    # Build producer map
    producers = {}
    for node in graph.nodes:
        for out in node.outputs:
            producers[out.name] = node

    # Find vision backbone nodes (ancestors of hidden_state)
    vision_nodes = find_ancestors(hidden_state_tensor, graph.nodes, producers)
    print(f"  Vision backbone nodes (ancestors of hidden_state): {len(vision_nodes)}")

    # Find policy nodes (everything that is NOT a vision node)
    # But some nodes produce perception outputs (lane_lines, etc.) that branch off the vision backbone
    # Those should stay in the vision model

    # Actually let's check: how many nodes are NOT ancestors of hidden_state?
    policy_nodes = [n for n in graph.nodes if n not in vision_nodes]
    print(f"  Remaining nodes (policy + perception heads): {len(policy_nodes)}")

    # Check which inputs the vision subgraph needs
    vision_input_names = set()
    for node in vision_nodes:
        for inp in node.inputs:
            if inp.name in [i.name for i in graph.inputs]:
                vision_input_names.add(inp.name)
    print(f"\n  Vision model inputs: {vision_input_names}")

    # Check which inputs the policy subgraph needs
    policy_input_names = set()
    for node in policy_nodes:
        for inp in node.inputs:
            if inp.name in [i.name for i in graph.inputs]:
                policy_input_names.add(inp.name)
    print(f"  Policy model inputs: {policy_input_names}")

    # The hidden_state tensor bridges: produced by vision, consumed by policy
    policy_consumers_of_hs = []
    for node in policy_nodes:
        if hidden_state_tensor.name in [i.name for i in node.inputs]:
            policy_consumers_of_hs.append(node)
    print(f"\n  Policy nodes consuming hidden_state: {len(policy_consumers_of_hs)}")

    print(f"\n=== Split analysis ===")
    print(f"Total nodes: {len(graph.nodes)}")
    print(f"Vision nodes: {len(vision_nodes)} ({len(vision_nodes)*100//len(graph.nodes)}%)")
    print(f"Policy nodes: {len(policy_nodes)} ({len(policy_nodes)*100//len(graph.nodes)}%)")
    print(f"\nVision model would take: img, big_img, features_buffer")
    print(f"  Output: hidden_state (512 elements)")
    print(f"Policy model would take: hidden_state, desire_pulse, traffic_convention, action_t")
    print(f"  Output: plan (990), desire_state (8), action (4)")

    # Estimate: vision is ~40% of nodes, policy is ~60%
    # If the full model is 67ms (INT8 flash), vision might be ~27ms, policy ~40ms
    # But they can run on SEPARATE NPU cores in parallel!
    # Pipeline: vision on core 2 (27ms), policy on core 0 (40ms)
    # Total = max(27, 40) = 40ms? Or sequential = 67ms?
    #
    # Actually, policy DEPENDS on vision output (hidden_state), so they can't run in parallel
    # for the SAME frame. But frame N's policy can run while frame N+1's vision runs.
    # Pipeline: vision_N (27ms) → policy_N (40ms) || vision_N+1 (27ms)
    # Total per frame = max(27, 40) = 40ms = 25Hz
    #
    # But 40ms for policy seems too much. The 0.10 policy was only 3ms.
    # The 0.11 policy has attention (MatMul) layers that 0.10 didn't have.

    print(f"\n=== Estimated pipeline performance ===")
    full_ms = 67.2  # INT8 flash measured
    vision_pct = len(vision_nodes) / len(graph.nodes)
    policy_pct = len(policy_nodes) / len(graph.nodes)
    vision_est = full_ms * vision_pct
    policy_est = full_ms * policy_pct
    print(f"  Full model: {full_ms}ms")
    print(f"  Vision (est): {vision_est:.0f}ms ({vision_pct*100:.0f}% of nodes)")
    print(f"  Policy (est): {policy_est:.0f}ms ({policy_pct*100:.0f}% of nodes)")
    print(f"  Sequential: {vision_est + policy_est:.0f}ms (same as full)")
    print(f"  Pipelined: ~{max(vision_est, policy_est):.0f}ms ({1000/max(vision_est, policy_est):.0f} Hz)")

    print(f"\n  Note: These are rough estimates. Node count doesn't perfectly")
    print(f"  correlate with compute time (Conv layers are heavier than Gemm).")
    print(f"  Need to actually build and benchmark both parts.")

if __name__ == "__main__":
    main()
