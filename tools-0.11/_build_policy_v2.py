import onnx
import onnx_graphsurgeon as gs
import numpy as np
from onnx import shape_inference

INPUT = "/work/selfdrive/modeld/models/driving_supercombo.onnx"
OUTPUT = "/work/selfdrive/modeld/models/driving_supercombo_policy_v2.onnx"

print("Loading original...")
graph = gs.import_onnx(onnx.load(INPUT))

producers = {}
consumers_map = {}
for node in graph.nodes:
    for out in node.outputs:
        producers[out.name] = node
    for inp in node.inputs:
        consumers_map.setdefault(inp.name, []).append(node)

# Find hidden_state
hidden_state = None
node_linear = None
for node in graph.nodes:
    if node.name == 'node_linear' and node.op == 'Gemm':
        hidden_state = node.outputs[0]
        node_linear = node
        break

print(f"Hidden state: {hidden_state.name}")

# Find consumers of hidden_state
hs_consumers = consumers_map.get(hidden_state.name, [])
print(f"HS consumers: {len(hs_consumers)}")

# Trace forward
start_names = ["features_buffer", "desire_pulse", "traffic_convention", "action_t"]
for n in hs_consumers:
    for o in n.outputs:
        start_names.append(o.name)

def find_descendants(start_names, csm):
    desc = set()
    queue = list(start_names)
    visited = set()
    while queue:
        t = queue.pop(0)
        if t in visited: continue
        visited.add(t)
        for n in csm.get(t, []):
            desc.add(n.name)
            for o in n.outputs: queue.append(o.name)
    return desc

policy_desc = find_descendants(start_names, consumers_map)
for n in hs_consumers:
    policy_desc.add(n.name)
policy_desc.discard(node_linear.name)

policy_nodes = [n for n in graph.nodes if n.name in policy_desc]
print(f"Policy nodes: {len(policy_nodes)}")

# Find outputs
plan_t = ds_t = act_t = None
for node in policy_nodes:
    if node.name == 'node_mul_18': plan_t = node.outputs[0]
    if node.name == 'node_linear_55': ds_t = node.outputs[0]
    if node.name == 'node_mul_25': act_t = node.outputs[0]

print(f"Outputs: plan={plan_t}, ds={ds_t}, act={act_t}")

# Build inputs
policy_inputs = []
for inp in graph.inputs:
    if inp.name in ["features_buffer", "desire_pulse", "traffic_convention", "action_t"]:
        policy_inputs.append(inp)
hs_input = gs.Variable(name="linear", dtype=np.float32, shape=[1, 512])
policy_inputs.append(hs_input)

# Build graph
policy_graph = gs.Graph(
    nodes=policy_nodes,
    inputs=policy_inputs,
    outputs=[plan_t, ds_t, act_t],
    opset=graph.opset,
)

# KEY: cleanup and toposort
print("Cleaning up and topological sort...")
policy_graph = policy_graph.cleanup().toposort()

policy_onnx = gs.export_onnx(policy_graph)
policy_onnx.ir_version = 9

# Shape inference
try:
    policy_onnx = shape_inference.infer_shapes(policy_onnx)
except Exception as e:
    print(f"Shape inference warning: {e}")

onnx.save(policy_onnx, OUTPUT)
print(f"SAVED: {OUTPUT}")
print(f"Nodes: {len(policy_graph.nodes)}")
