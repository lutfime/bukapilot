#!/usr/bin/env python3
"""Find EXACTLY which layers in the OPM10V3 policy heads overflow fp16 (>65504).

Per OPM10V3-CPP-3SPLIT-FINDINGS.md "HOW TO PROPERLY FIX THE MODEL" Step 1.
Uses ONNX Runtime with the original opset-20 ONNX (native Gelu).
"""
import sys, os
import numpy as np
import onnx
import onnxruntime as ort

FP16_MAX = 65504.0
MODELS_DIR = "selfdrive/modeld/models"

def add_intermediate_outputs(onnx_path):
    m = onnx.load(onnx_path)
    existing = {o.name for o in m.graph.output}
    for node in m.graph.node:
        for out_name in node.output:
            if out_name and out_name not in existing:
                for vi in m.graph.value_info:
                    if vi.name == out_name:
                        m.graph.output.append(vi)
                        existing.add(out_name)
                        break
    out_path = onnx_path.replace(".onnx", "_debug.onnx")
    onnx.save(m, out_path)
    return out_path

def run_diagnostic(onnx_path, model_name, n_frames=20):
    print(f"\n{'='*70}")
    print(f"  Diagnosing: {model_name}")
    print(f"{'='*70}")
    
    debug_path = add_intermediate_outputs(onnx_path)
    sess = ort.InferenceSession(debug_path)
    input_names = [i.name for i in sess.get_inputs()]
    input_types = [i.type for i in sess.get_inputs()]
    output_names = [o.name for o in sess.get_outputs()]
    print(f"  Inputs: {input_names}")
    print(f"  Input types: {input_types}")
    print(f"  Total outputs (incl intermediate): {len(output_names)}")
    
    rng = np.random.RandomState(42)
    overflow_layers = {}
    max_values = {}
    
    for frame in range(n_frames):
        desire_pulse = np.zeros((1, 25, 8), dtype=np.float16)
        traffic_convention = np.array([[1.0, 0.0]], dtype=np.float16)
        base = (rng.randn(512) * 0.3).astype(np.float16)
        features_buffer = np.zeros((1, 25, 512), dtype=np.float16)
        for t in range(25):
            base = np.clip(base + (rng.randn(512) * 0.05).astype(np.float16), np.float16(-1.5), np.float16(1.5))
            features_buffer[0, t] = base
        
        feeds = {
            "desire_pulse": desire_pulse,
            "traffic_convention": traffic_convention,
            "features_buffer": features_buffer,
        }
        
        results = sess.run(output_names, feeds)
        
        for name, val in zip(output_names, results):
            val = np.abs(val.astype(np.float64))
            mx = float(val.max()) if val.size > 0 else 0.0
            if mx > FP16_MAX:
                overflow_layers[name] = overflow_layers.get(name, 0) + 1
            if name not in max_values or mx > max_values[name]:
                max_values[name] = mx
    
    print(f"\n  Scanned {n_frames} frames. fp16 max = {FP16_MAX:.0f}")
    
    if not overflow_layers:
        print(f"  NO overflow detected in any layer with these inputs.")
        print(f"  (Real driving data may differ — but these are in-distribution features)")
    else:
        print(f"\n  ❌ {len(overflow_layers)} layers OVERFLOW (> {FP16_MAX:.0f}):")
        print(f"  {'Layer':<55} {'Max Value':>12} {'Frames %':>10}")
        print(f"  {'-'*55} {'-'*12} {'-'*10}")
        for name in sorted(overflow_layers.keys(), key=lambda n: -max_values[n]):
            mx = max_values[name]
            pct = overflow_layers[name] / n_frames * 100
            print(f"  {name:<55} {mx:>12.1f} {pct:>9.0f}%")
    
    print(f"\n  Top-15 largest activations:")
    print(f"  {'Layer':<55} {'Max Value':>12}")
    print(f"  {'-'*55} {'-'*12}")
    for name in sorted(max_values.keys(), key=lambda n: -max_values[n])[:15]:
        flag = " ⚠️ OVERFLOW" if max_values[name] > FP16_MAX else ""
        print(f"  {name:<55} {max_values[name]:>12.1f}{flag}")
    
    return overflow_layers

if __name__ == "__main__":
    for onnx_file, name in [
        ("driving_on_policy.onnx", "on_policy"),
        ("driving_off_policy.onnx", "off_policy"),
    ]:
        path = os.path.join(MODELS_DIR, onnx_file)
        if os.path.exists(path):
            run_diagnostic(path, name)
        else:
            print(f"\n  SKIP {name}: not found")
    
    print(f"\n{'='*70}")
    print("Use overflow layer names to add targeted Clip nodes (Step 2 of fix)")
    print(f"{'='*70}")
