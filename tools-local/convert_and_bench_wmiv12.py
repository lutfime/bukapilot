#!/usr/bin/env python3
"""Convert WMI v12 (2-file split: vision + policy) to RKNN + benchmark in one pass.

WMI v12 is a community finetune of the 0.10.3 policy head on the same vision backbone.
Opset 17 (no Gelu rewrite needed), same I/O as production 0.10.3.
"""
import os, sys, pickle, base64, time
from pathlib import Path
import numpy as np
import onnx
from rknn.api import RKNN

MODELS_DIR = Path(__file__).resolve().parents[1] / "selfdrive" / "modeld" / "models_dev" / "wmiv12"
TARGET_PLATFORM = "rk3588"

models = [
    ("vision", "driving_vision.onnx", "driving_vision_wmiv12.rknn"),
    ("policy", "driving_policy.onnx", "driving_policy_wmiv12.rknn"),
]

def cast_uint8(path):
    m = onnx.load(str(path))
    from onnx import TensorProto
    for inp in m.graph.input:
        if inp.type.tensor_type.elem_type == TensorProto.UINT8:
            inp.type.tensor_type.elem_type = TensorProto.FLOAT
    onnx.save(m, str(path))

def extract_metadata(path):
    m = onnx.load(str(path))
    output_slices_b64 = None
    model_checkpoint = None
    for prop in m.metadata_props:
        if prop.key == 'output_slices': output_slices_b64 = prop.value
        elif prop.key == 'model_checkpoint': model_checkpoint = prop.value
    output_slices = pickle.loads(base64.b64decode(output_slices_b64))
    def shape_of(ts):
        return tuple(d.dim_value if d.HasField('dim_value') else d.dim_param for d in ts.type.tensor_type.shape.dim)
    return {
        'model_checkpoint': model_checkpoint,
        'output_slices': output_slices,
        'input_shapes': {inp.name: shape_of(inp) for inp in m.graph.input},
        'output_shapes': {out.name: shape_of(out) for out in m.graph.output},
    }

total_sim_ms = 0
for name, onnx_file, rknn_file in models:
    onnx_path = MODELS_DIR / onnx_file
    rknn_path = MODELS_DIR / rknn_file
    meta_path = MODELS_DIR / rknn_file.replace(".rknn", "_metadata.pkl")

    print(f"\n{'='*60}")
    print(f"  {name}")
    print(f"{'='*60}")

    # Cast UINT8 inputs (vision only)
    if name == "vision":
        cast_uint8(onnx_path)

    # Extract metadata
    metadata = extract_metadata(onnx_path)
    print(f"  checkpoint: {metadata['model_checkpoint']}")

    # RKNN convert
    rknn = RKNN(verbose=False)
    rknn.config(mean_values=[[]], std_values=[[]], target_platform=TARGET_PLATFORM, quantized_dtype="w16a16i")
    ret = rknn.load_onnx(model=str(onnx_path))
    if ret != 0: print(f"  FAIL load_onnx: {ret}"); continue
    ret = rknn.build(do_quantization=False)
    if ret != 0: print(f"  FAIL build: {ret}"); continue
    ret = rknn.export_rknn(str(rknn_path))
    if ret != 0: print(f"  FAIL export: {ret}"); continue
    print(f"  RKNN: {rknn_path.name} ({rknn_path.stat().st_size/1e6:.1f} MB)")

    with open(meta_path, "wb") as f:
        pickle.dump(metadata, f)

    # Benchmark in simulator
    rknn.init_runtime(target=None)
    input_shapes = metadata["input_shapes"]
    dummy_inputs = []
    for inp_name, shape in input_shapes.items():
        if "img" in inp_name:
            arr = np.random.randint(0, 256, shape, dtype=np.uint8).astype(np.float16)
        else:
            arr = np.random.randn(*shape).astype(np.float16)
        dummy_inputs.append(np.ascontiguousarray(arr))

    for _ in range(3):
        rknn.inference(inputs=dummy_inputs, data_format="nchw")
    times = []
    for _ in range(10):
        t0 = time.perf_counter()
        rknn.inference(inputs=dummy_inputs, data_format="nchw")
        t1 = time.perf_counter()
        times.append((t1 - t0) * 1000)
    avg = np.mean(times)
    total_sim_ms += avg
    ratio = 2.37 if "img" in str(input_shapes) else 1.35
    print(f"  sim: {avg:.1f}ms  est device: ~{avg/ratio:.1f}ms (/{ratio:.2f})")
    rknn.release()

sep = "=" * 60
print(f"\n{sep}")
print(f"WMI v12 (2-file split)")
print(f"  Total sim: {total_sim_ms:.1f}ms")
est = total_sim_ms*0.85/2.37 + total_sim_ms*0.15/1.35  # vision-heavy estimate
print(f"  Est device total: ~{est:.0f}ms = ~{1000/est:.1f} Hz")
print(f"  Reference 0.10.3: 32.8ms / 30.5 Hz")
print(f"  Reference OPM10V3: ~32ms / ~31 Hz")
print(f"{sep}")
