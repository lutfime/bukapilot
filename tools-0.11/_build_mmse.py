import os, time, sys
from rknn.api import RKNN

ONNX = "/work/selfdrive/modeld/models/driving_supercombo.onnx"
DATASET = "/work/tools-local/calibration_frames/dataset.txt"
OUTPUT = "/work/tools-local/models_store/driving_supercombo_int8_mmse_flash.rknn"

rknn = RKNN(verbose=False)
rknn.config(
    mean_values=[[]], std_values=[[]],
    target_platform="rk3588", optimization_level=3,
    float_dtype="float16",
    quantized_dtype="w8a8", quantized_method="channel",
    quantized_algorithm="mmse",
    enable_flash_attention=True, remove_reshape=True,
)

ret = rknn.load_onnx(model=ONNX)
print(f"load_onnx: {ret}", flush=True)
if ret != 0:
    sys.exit(1)

t0 = time.time()
print("Building MMSE (expect 15-30 min)...", flush=True)
ret = rknn.build(do_quantization=True, dataset=DATASET)
elapsed = time.time() - t0
print(f"build: {ret} ({elapsed:.0f}s)", flush=True)
if ret != 0:
    sys.exit(1)

ret = rknn.export_rknn(OUTPUT)
print(f"export: {ret}", flush=True)
if ret == 0:
    size = os.path.getsize(OUTPUT) / 1e6
    print(f"SAVED: {OUTPUT} ({size:.1f} MB)", flush=True)
rknn.release()
print("DONE", flush=True)
