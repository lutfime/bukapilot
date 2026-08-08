# Converting comma's Driving Model to RKNN (for the Kommu KA2 / RK3588 NPU)

> Reusable guide. Follow this whenever comma ships a new driving model checkpoint
> (0.11.x, 0.12, etc.) and you want to run it on the KA2 NPU.
>
> **Current status:** The 0.11 supercombo model does NOT work on the KA2 at any precision.
> C API benchmarks: FP16 ~101ms est., INT8 **79.5ms (12.6Hz)** — both below the 20Hz target.
> INT8 gave 1.27× speedup (not the theoretical 2× — memory-bound layers don't benefit).
> The 0.10 split model (**32.8ms / 30.5Hz** via C API) remains the production choice.
> See §4 for the full analysis and §11 for how to benchmark.
>
> Last successful conversion: openpilot 0.11.2 standard `driving_supercombo.onnx` →
> `driving_supercombo.rknn` (135 MB, FP16). Verified 2026-08-04, benchmarked 2026-08-07.

---

## TL;DR — the path that actually works on Apple Silicon

```bash
# On your Mac (Apple Silicon), using Docker with an aarch64 Ubuntu container:
brew install colima docker && colima start --cpu 4 --memory 4 --disk 20   # one time
docker run --rm --platform linux/arm64 -v "$PWD":/work -w /work \
  -e REPO_ROOT_OVERRIDE=/work ubuntu:22.04 bash -c '
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -qq && apt-get install -y -qq python3 python3-pip python3-dev libgl1 libglib2.0-0 cmake build-essential
    pip3 install --no-input "https://github.com/airockchip/rknn-toolkit2/raw/master/rknn-toolkit2/packages/arm64/rknn_toolkit2-2.3.2-cp310-cp310-manylinux_2_17_aarch64.manylinux2014_aarch64.whl"
    pip3 install --no-input onnx numpy
    cd /work && python3 tools-local/convert_011_model_to_rknn.py
  '
# Output: selfdrive/modeld/models/driving_supercombo.rknn + _metadata.pkl
```

The rest of this doc explains what it does, the obstacles it handles, and how to adapt it
for a future model.

---

## 1. Why conversion is needed

comma's openpilot runs the driving model via **tinygrad on a GPU** (Snapdragon 845 in
comma 3X/four). The Kommu KA2 uses a **Rockchip RK3588** whose fast compute is its **NPU**,
reached only through Rockchip's RKNN runtime. So the model must be converted from
comma's `.onnx` format to Rockchip's `.rknn` format.

- **The good news:** RKNN-Toolkit2 reads ONNX natively. Format conversion is a supported
  path, not reverse-engineering.
- **The catch:** RKNN-Toolkit2 does NOT run on macOS directly. But Rockchip publishes an
  **aarch64** wheel — which runs natively on Apple Silicon inside a Docker container
  (no Rosetta, no emulation, full speed). See §3 for the working setup.

---

## 2. What the conversion pipeline does (in order)

`tools-local/convert_011_model_to_rknn.py` runs these steps:

1. **Download** `driving_supercombo.onnx` from comma's repo (or use a staged copy).
2. **Extract metadata** — the `output_slices` (where each named output lives in the flat
   output tensor) ship as a base64-encoded pickle inside the ONNX's `metadata_props`.
   The script reads them directly; no manual slice math needed.
3. **Cast UINT8 image inputs → FLOAT.** RKNN's w16a16i build rejects UINT8
   (`Not Support Dtype: 2`). The inputs `img`/`big_img` are declared UINT8 in comma's
   model, but bukapilot's runtime casts them to float16 before feeding anyway, so
   changing the declared type to FLOAT is safe. See `cast_uint8_inputs_to_float.py`.
4. **Rewrite native Gelu ops → erf subgraphs** (`rewrite_gelu_for_opset19.py`).
   comma ships the model at **opset 20**, but RKNN-Toolkit2 only supports **opset ≤ 19**.
   The only opset-20-only op the model uses is native `Gelu` (added in opset 20). The
   rewriter replaces each `Gelu(x)` with `0.5 * x * (1 + erf(x / sqrt(2)))` (8 nodes),
   then downconverts to opset 19. For the 0.11 model this replaced 39 Gelu nodes
   (481 → 754 total nodes).
5. **Convert** with `rknn.config(target_platform='rk3588', quantized_dtype='w16a16i')` →
   `load_onnx` → `build(do_quantization=False)` → `export_rknn`. This produces an **FP16**
   model (the `quantized_dtype` is ignored when `do_quantization=False`). See §4 for why
   and §4a for how to switch to INT8 with calibration images.
6. **Write metadata pickle** (`driving_supercombo_metadata.pkl`) containing the
   `input_shapes`, `output_shapes`, `output_slices`, and `model_checkpoint` — the runtime
   loader reads this to know how to slice the flat output.

**For a future model:** steps 1, 2, 5, 6 are automatic. Steps 3 and 4 may or may not be
needed depending on what comma changes (see §5 — "When comma ships a new model").

---

## 3. Where to run it (the environment problem)

RKNN-Toolkit2 doesn't run on macOS natively. But the **aarch64** wheel runs **natively on
Apple Silicon** inside a Docker container (no Rosetta, no emulation). That's the path that
actually worked for us.

### ✅ Docker with the aarch64 wheel on Apple Silicon (what we used — recommended)

This is native-speed because Apple Silicon and the aarch64 container share the same
architecture. No emulation overhead, no crash.

```bash
# Install Colima + Docker CLI (one time):
brew install colima docker
colima start --cpu 4 --memory 4 --disk 20

# Run the conversion (aarch64 native — fast, no emulation crash):
docker run --rm --platform linux/arm64 \
  -v "$PWD":/work -w /work \
  -e REPO_ROOT_OVERRIDE=/work \
  ubuntu:22.04 \
  bash -c '
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -qq && apt-get install -y -qq python3 python3-pip python3-dev \
      libgl1 libglib2.0-0 cmake build-essential
    pip3 install --no-input \
      "https://github.com/airockchip/rknn-toolkit2/raw/master/rknn-toolkit2/packages/arm64/rknn_toolkit2-2.3.2-cp310-cp310-manylinux_2_17_aarch64.manylinux2014_aarch64.whl"
    pip3 install --no-input onnx numpy
    cd /work
    python3 tools-local/convert_011_model_to_rknn.py
  '
```

**Why aarch64 works:** Rockchip publishes an aarch64 wheel
(`rknn-toolkit2/packages/arm64/...`). Apple Silicon is also aarch64, so it runs natively
in the container — full speed, no translation.

### Alternative: any native x86_64 Linux box
An Intel/AMD PC, x86 cloud VPS, or Intel Mac running Ubuntu. Just `pip install` the
x86 wheel directly — no Docker needed. Faster setup if you already have a Linux box.

### ❌ Does NOT work
- macOS native (no RKNN-Toolkit2 build for macOS)
- **Docker x86 emulation on Apple Silicon** — the x86 wheel installs but `pip install`
  becomes a defunct/zombie process after ~7 min of CPU (the native C++ RKNN runtime
  doesn't survive x86-on-arm Rosetta translation). **Always use the aarch64 wheel on
  Apple Silicon.**
- The KA2 device itself (it's aarch64, but RKNN-Toolkit2 the *converter* needs more RAM/
  disk than the device has; the device only runs RKNN-Lite2 the *runtime*)

---

## 4. Quantization & calibration (CORRECTED 2026-08-07, after on-device benchmarking)

### The three conversion paths and when each needs calibration images

| Path | `config()` dtype | `build()` call | Calibration images? | Speed | Accuracy |
|------|-----------------|----------------|---------------------|-------|----------|
| **FP16** | *(ignored)* | `do_quantization=False` | ❌ **Not needed** | 132ms (7.5Hz) | ~100% of FP32 |
| **INT16** | `w16a16i` | `do_quantization=True` + dataset | ✅ **Needed** | ~132ms (same datapath) | ~99.5% |
| **INT8** | `w8a8` | `do_quantization=True` + dataset | ✅ **Needed** | ~66ms (15Hz est.) | ~95-98% (risky) |

**FP16 needs no images** — it's a pure format conversion (float32 weights → float16),
no range-finding. INT16 and INT8 both need calibration images because quantization must
measure real activation ranges to map values into fewer buckets without breaking outputs.

### ⚠️ The `do_quantization=False` detail

Per Rockchip's documentation:

> *"For a floating-point model, `do_quantization=False` will not perform quantization
> operations, but will convert the weights from float32 to float16, with almost no
> accuracy loss."*

So when `do_quantization=False`, the `quantized_dtype` setting in `config()` is **ignored
entirely**. The model ships as FP16 regardless. The current script uses this path (FP16,
no calibration). An empty `quant_tab` in the RKNN metadata confirms no scales were computed.

### RK3588 NPU hardware reality (measured + documented)

| Datatype | Single-core throughput | Speed vs FP16 | Source |
|----------|------------------------|---------------|--------|
| FP16 (IEEE half) | ~900 GFLOPS | 1.0× | clehaxze benchmark (2024) |
| INT16 (`w16a16i` quantized) | ~900 GFLOPS | **~1.0× — NO SPEEDUP** | Same datapath as FP16 |
| INT8 (`w8a8`) | ~1.8 TOPS | **~2.0×** | Packs 2 INT8 ops/cycle in same MAC array |
| INT4 | ~4 TOPS peak | ~2.2-2.5× | Accuracy-destroying |

**The RK3588 NPU has ONE 16-bit MAC datapath.** FP16 and INT16 share it at the same
throughput (~3 TOPS across 3 cores). The 6 TOPS headline is INT8-only — the hardware
packs two INT8 operations per cycle. There is no separate INT16 unit.

**Conclusion: INT16-quantized gives approximately ZERO speedup over FP16.** Both use the
same datapath. INT16 quantization only helps model size and marginal accuracy tuning, not
speed. Do not pursue INT16 for speed.

### The only path to real speedup: INT8 (`w8a8`)

INT8 is the only datatype that uses the full 6 TOPS datapath (~2× faster than FP16/INT16).
At 2× speedup, the supercombo would drop from **132ms → ~66ms** (~15Hz). This is still
below the 20Hz (50ms) target, but much closer than FP16.

**INT8 accuracy risk:** RKPilot/sunnypilot-pc rejected w8a8 for driving quality on this
model class. The steering/braking decisions may degrade. This is untested on our specific
supercombo conversion — the only way to know is to convert with real calibration data and
test-drive.

**Is INT8 0.11 better than FP16 0.10?** Unknown — it's a tradeoff:
- 0.10: 40ms/25Hz, known-good quality, tested on X70
- 0.11 INT8: ~66ms/15Hz, potentially smarter model (action head, better planning), but
  untested accuracy + slower rate + calibration needed
- The answer requires a test conversion + test drive to evaluate

### On-device benchmark results (2026-08-07, measured on real KA2 NPU, outputs verified)

**IMPORTANT: Two benchmark methods give very different numbers. Always use the C API for
production-equivalent results.**

| Model | Size | Datatype | Python rknnlite | **C API (production)** | Fits 20Hz? |
|-------|------|----------|-----------------|----------------------|------------|
| 0.10 vision | 76MB | FP16 | 65ms | **29.7ms** | ✅ |
| 0.10 policy | 16MB | FP16 | 4.2ms | **3.1ms** | ✅ |
| **0.10 total** | 92MB | FP16 | 69ms (14.4Hz) | **32.8ms (30.5Hz)** | ✅ |
| 0.11 supercombo | 135MB | FP16 | 132ms (7.5Hz) | ~101ms (est.) | ❌ |
| **0.11 supercombo** | 71MB | **INT8** (w8a8) | 107ms (9.3Hz) | **79.5ms (12.6Hz)** | ❌ |

> **Why Python and C differ so much:** The Python `rknnlite` wrapper adds 30-100% overhead
> (Python→C bridge, object creation, data copies, GIL). The C API (`rknn_init` → `rknn_run`
> → `rknn_outputs_get`) is what production modeld uses — zero overhead. **Always benchmark
> with the C API** (`tools-local/bench_rknn.c`). The Python benchmark is only for quick
> sanity checks.

**INT8 verdict:** At 79.5ms / 12.6Hz (C API), the 0.11 INT8 supercombo is **not viable for
production driving**. It's below the 20Hz target and would drop ~37% of frames. The 0.10
split model (32.8ms / 30.5Hz) has huge headroom and remains the production choice.

However, INT8 did provide a real speedup: **79.5ms vs ~101ms estimated FP16** (1.27× in C,
matching the 1.23× measured in Python). The conversion is correct — the supercombo is simply
too large for the RK3588 NPU.

> **Note on the `inf` red herring:** Earlier benchmarks showed `max=inf` in the supercombo
> output. This was a **benchmark bug, not a model defect** — feeding NHWC-transposed image
> data to a model compiled as NCHW corrupts the computation. When fed correct NCHW inputs,
> the output is 100% valid (2580/2580 finite, max=134.875). The model itself is numerically
> sound.

### NPU core mask reality

`RKNN_NPU_CORE_0_1_2` is a **scheduling affinity**, not model parallelism. A single
inference runs on ONE core; the mask only widens the pool for concurrent separate
inferences. Measured: core 2 only = 142ms, all 3 cores = 138ms (noise, no speedup).

The RK3588's 3-core design is for **concurrent processes**: driving on core 2,
dmonitoring on cores 0+1. A single fused model cannot be split across cores.

### Sources
- [Rockchip RKNPU User Guide (RKNN SDK V2.0.0beta0, EN)](https://www.scribd.com/document/774992182) — the `do_quantization=False` → float32-to-float16 statement
- [Benchmarking RK3588 NPU (clehaxze.tw, 2024)](https://clehaxze.tw/gemlog/2024/02-14-benchmarking-rk3588-npu-matrix-multiplcation-performance-ep2.gemi) — INT8 = 2× FP16 (1.8 TOPS vs 900 GFLOPS)
- `third_party/rknpu/include/rknn_api.h` — core_mask is scheduling affinity, not model split
- `docs/development/modeld_rknn.md` — intended core design (driving=core2, dmonitoring=0+1)

---

## 4a. How calibration images work (for INT8/INT16 conversion)

### What the images actually do

When you quantize, the converter needs to know the **range of values** each layer produces
(min/max activations). It learns these by running your images through the model:

1. Converter takes the FP16 model + a dataset of real driving frames
2. For each calibration image, it runs a forward pass and records every layer's activation
   range (min, max, histogram)
3. It uses those ranges to compute a **scale + zero-point** for each tensor — the mapping
   that squeezes values into 256 INT8 buckets with minimal information loss
4. These scales are baked into the final `.rknn` file (the `quant_tab`)
5. At runtime, the NPU uses these scales to interpret INT8 values back into real numbers

**Bad calibration = bad scales = wrong driving decisions.** If all your images are daytime
highway and the car drives at night in rain, the scales won't cover that distribution and
the model outputs garbage in those conditions. Variety is the entire point.

### What images you need

The 0.11 supercombo has **two camera inputs**, so you need frames from **both**:

| Input name | Source | Camera file | FOV |
|-----------|--------|-------------|-----|
| `img` | narrow road cam | `fcamera.hevc` | narrow |
| `big_img` | wide road cam | `ecamera.hevc` | wide |

Calibrating with only one camera degrades the other input. Both are mandatory.

### How many images

- **Minimum viable:** ~200 images
- **Good:** 500-1000
- **More than 1000:** diminishing returns, just slows conversion

We have ~879 extracted frames available (17 HEVC segments × ~55 frames each × 2 cameras,
minus the night gap). A curated 500-frame set (day/night mix) is the target.

### What variety matters

- **Day AND night** — different exposure, lighting, headlight bloom
- **Highway AND city** — different scene complexity, lane markings, traffic
- **Straight AND curves** — different geometry
- **Different speeds** — motion, blur patterns
- **Skip parking frames** — first ~5 minutes of a route are often parked (device booted
  but car stationary). These don't represent driving and waste calibration budget.

### Why raw frames look "dark"

The raw HEVC frames look darker than what you see on the device screen because the **UI
applies brightness/gamma enhancement at display time**. The model receives the raw frames
directly — it's trained on this raw data, so dark-looking raw frames are correct and
expected. Do not brighten/preprocess the calibration images.

### ⚠️ CHECK SPEED BEFORE DOWNLOADING (lesson learned)

**Always verify a route is actually driving before downloading its 70 MB HEVC files.**
Some routes are just the car parked with the device running — they're useless for
calibration and waste download time. We downloaded 8 segments of `2026-08-03--08-43-13`
before realizing it was **parked the entire time** (0.0 m/s for all 600 frames).

**How to check:** Each segment has a small `qlog` file (~500KB–2MB) that contains
`carState.vEgo` (vehicle speed). Download that first, check if the car was moving, then
only download HEVC if it was.

The script `tools-local/check_route_speed.py` does this:
```bash
# Install deps (one time):
pip3 install pycapnp zstandard

# Download a qlog via MCP, then check speed:
python3 /tmp/mcp_chunk_dl.py /data/media/0/realdata/<ROUTE>--<SEG>/qlog /tmp/test_qlog
cd /path/to/bukapilot && python3 tools-local/check_route_speed.py /tmp/test_qlog

# Output:
#   parked_qlog:
#     600 samples | vEgo min=0.0 max=0.0 mean=0.0 m/s
#     moving(>1m/s): 0/600 (0%) -> PARKED
#
#   moving_qlog:
#     601 samples | vEgo min=2.1 max=16.1 mean=12.5 m/s
#     moving(>1m/s): 601/601 (100%) -> DRIVING
```

**Rule:** only download HEVC from segments where `verdict = DRIVING` (vEgo > 1 m/s for
>50% of samples). A segment is ~1 minute of driving, so even within a driving route,
some segments near start/end may be parked (warming up, parking). Check each one.

> **Note on qlog file extension:** older Kommu segments use uncompressed `qlog` /
> `rlog` (no `.zst` extension). Newer ones use `qlog.zst` / `rlog.zst`. The script
> auto-detects zstd compression by magic bytes (`28 B5 2F FD`).

### Our calibration dataset (as of 2026-08-07)

**Downloaded to Mac (17 raw HEVC files, ~1.1 GB):**
```
tools-local/calibration_frames/raw/
├── d_2026-08-03--08-43-13__s{05,15,25,32}_{fcamera,ecamera}.hevc  (8 files — ⚠️ PARKED, useless)
├── d_2026-08-05--14-04-21__s{03,12,20}_{fcamera,ecamera}.hevc     (6 files, day, DRIVING ✓)
└── n_2026-08-05--00-26-14__s{10,25}_{fcamera,ecamera}.hevc        (3 files, night; s10 fcamera partial)
```

> **The `2026-08-03` files (8 of 17) are parked scenes — do not use for calibration.**
> They should be deleted or ignored. This was the mistake that prompted the speed-check
> section above. Next time: check qlog speed before downloading HEVC.

**Extracted to JPEGs (879 images, ready to curate):**
```
tools-local/calibration_frames/extracted/
└── <source_segment_name>/frame_NNNN.jpg   (one folder per HEVC file)
```

**Status:** Frames extracted, awaiting manual curation (user picks the good driving frames,
discards parking/transitions). The `extracted/` folder can be opened in Finder for browsing.

**To resume downloads** (3 night files still missing + replace parked route): see
`tools-local/calibration_frames/RESUME-DOWNLOADS.txt`.

### Reference comparison

Sample frames from comma devices (for brightness/FOV comparison) are in:
```
tools-local/calibration_frames/compare/
```
Includes: comma2k19 sample (2018 EON), comma 3X/4 YouTube thumbnails (2025), and our
day/night frames from both cameras. Brightness analysis shows our ecamera (wide) is
comparable to comma devices; our fcamera (narrow) runs darker (different sensor/lens).

---

## 4b. The `rknn.config()` parameters explained

```python
rknn.config(
    mean_values=[[]],            # per-channel mean subtraction (empty = none)
    std_values=[[]],             # per-channel std divisor (empty = none)
    target_platform='rk3588',    # which NPU generation to compile for
    quantized_dtype='w16a16i',   # target dtype IF do_quantization=True
    quantized_method='channel',  # per-channel vs per-tensor quantization
    optimization_level=3,        # RKNN graph optimization (0-3, 3=most aggressive)
)
```

| Parameter | What it does | Our value | Why |
|-----------|-------------|-----------|-----|
| `mean_values` | Subtracts a mean from each input channel before inference. Used when the model expects normalized inputs (e.g. `(pixel - 127.5) / 127.5`). | `[[]]` (empty) | The 0.11 model handles its own normalization internally. The inputs are raw float pixels. Adding mean/std would double-normalize. |
| `std_values` | Divides each channel by a std after mean subtraction. | `[[]]` (empty) | Same reason — model does its own normalization. |
| `target_platform` | Which Rockchip SoC to compile for. Determines available ops, NPU core count, supported dtypes. | `'rk3588'` | The KA2 uses RK3588 (3 NPU cores, 6 TOPS INT8). Other options: `rk3566`, `rk3568`, `rk3576`, `rk3588s`. |
| `quantized_dtype` | The target precision **if** `do_quantization=True`. Ignored when `do_quantization=False` (always FP16). | `'w16a16i'` | Currently inert (we use `do_quantization=False`). For INT8 conversion, change to `'w8a8'`. |
| `quantized_method` | How quantization scales are computed: `'channel'` (per-output-channel) or `'tensor'` (whole-tensor). | `'channel'` | Per-channel gives better accuracy — each weight channel gets its own scale instead of one scale for all. Standard for vision models. |
| `optimization_level` | RKNN graph optimizer aggressiveness (0-3). Higher = more fusion/constant-folding/simplification. | `3` | Maximum optimization for speed. Can occasionally cause bugs with unusual ops; if you hit a conversion error, try level 1 to isolate. |

### Additional RKNN options (not currently used, available for tuning)

These are settable in `config()` or `build()` but we don't use them yet:

| Option | What it does | When to try |
|--------|-------------|-------------|
| `quantized_algorithm='mmse'` | Uses Mean-Squared-Error minimization for scale computation (vs default min/max). Better accuracy, slower conversion. | INT8 conversion — can recover some accuracy lost by w8a8 |
| `enable_flash_attention=True` | RKNN's optimized attention kernel for transformer layers. Skips materializing the full attention matrix. | The supercombo has many attention layers — this could speed up FP16 too. **Untested.** |
| `remove_reshape=True` | Fuses Reshape ops into adjacent ops where possible. | Minor speed optimization. Safe to enable. |
| `auto_hybrid=True` (in `build()`) | Lets RKNN keep precision-sensitive layers in FP16 while quantizing the rest to INT8. | INT8 conversion — keeps critical layers accurate while still getting most of the speedup |
| `custom_string` | Pass raw RKNN config directives (advanced). | Rarely needed |

### Valid `quantized_dtype` values on RK3588 (RKNN-Toolkit2 v2.3.2)

```
w8a8        — INT8 weights + activations (fastest, precision risk)
w8a16       — INVALID on RK3588 (RKLLM-only, will error)
w16a16i     — INT16 weights + activations (same speed as FP16)
w16a16i_dfp — INT16 variant, dynamic fixed point
w4a16       — 4-bit weights + 16-bit activations (lowest precision)
```

> **Bikeshed warning:** several dtype strings you might guess are INVALID:
> - `'fp'` → rejected (`Invalid quantized_dtype`)
> - `'w8a16'` → rejected on RK3588 (`not support in 'rk3588'!`) — it's RKLLM-only
> - `'fp16'` → not a valid value at all
>
> The valid set is exactly: `['w8a8', 'w8a16', 'w16a16i', 'w16a16i_dfp', 'w4a16']`.

---

## 5. When comma ships a new model (0.11.3, 0.12, etc.)

The pipeline is mostly automatic, but watch for these changes comma might make:

### If they change the model file location/name
Update `ONNX_URL` in `convert_011_model_to_rknn.py`. Currently:
```
https://github.com/commaai/openpilot/raw/master/openpilot/selfdrive/modeld/models/driving_supercombo.onnx
```
(Following the redirect with `curl -L` or urlretrieve is required — `raw.githubusercontent.com`
returns the LFS pointer, not the file.)

### If they bump the opset again (e.g. opset 21)
- Check what new ops the higher opset added
- If `Gelu` is still the only blocker, `rewrite_gelu_for_opset19.py` handles it
- If they added a *different* op with no opset-19 predecessor, you'll need to write a new
  rewriter for that op (same pattern: replace it with a subgraph of opset-19 ops)

### If they change the input contract (add/remove inputs, change shapes)
- The metadata extraction handles this automatically — `output_slices` and `input_shapes`
  come from the model itself
- **The runtime loader (`modeld.py` / the runner) may need updates** to feed new inputs.
  This is the part that's device-specific (see `x70-test-NOTES.md` "exact modeld.py changes").

### If they ship a new "big model" variant
The standard model is the right target for the KA2. The "big model" (e.g. 880M params in
0.11.2) is ~1.7 GB FP16 and needs an external GPU — it won't fit the RK3588 NPU. Don't
attempt it.

### If RKNN-Toolkit2 itself updates
Check the wheel URLs in §3 — bump the version number in the URL. The airockchip repo
publishes wheels at:
```
https://github.com/airockchip/rknn-toolkit2/tree/master/rknn-toolkit2/packages/arm64
```

---

## 6. Verifying the converted model

Before pushing to the device, sanity-check the output:

```bash
# Verify the .rknn file has the right magic header
python3 -c "print(open('selfdrive/modeld/models/driving_supercombo.rknn','rb').read(4))"
# Should print: b'RKNN'

# Verify the metadata is readable and has the expected slices
python3 -c "
import pickle
m = pickle.load(open('selfdrive/modeld/models/driving_supercombo_metadata.pkl','rb'))
print('output_shape:', m['output_shapes'])
print('action slice:', m['output_slices'].get('action'))
print('input_shapes:')
for k,v in m['input_shapes'].items(): print(f'  {k}: {v}')
"
```

On-device verification (after loading):
- **20 Hz check:** `modelV2.modelExecutionTime` should average ≤ 50 ms
  (see `x70-test-NOTES.md` "The 20 Hz question")
- **CPU fallback check:** `logcat | grep -i rknn` — watch for runtime fallback warnings
  (those drag down the rate)

---

## 7. The files involved (reference)

| File | Purpose |
|------|---------|
| `tools-local/convert_011_model_to_rknn.py` | Main pipeline: download → preprocess → convert → export |
| `tools-local/rewrite_gelu_for_opset19.py` | Replaces native Gelu with erf subgraph (needed for opset ≤19) |
| `tools-local/cast_uint8_inputs_to_float.py` | Casts UINT8 image inputs → FLOAT (needed for w16a16i) |
| `tools-local/inspect_011_onnx.py` | Reads the ONNX structure (inputs/outputs/ops) — useful for diagnosing new models |
| `tools-local/run_conversion_in_docker.sh` | One-command Docker invocation wrapper |

The output files (these go on the device):
| File | Purpose |
|------|---------|
| `selfdrive/modeld/models/driving_supercombo.rknn` | The converted model (load via RKNN-Lite2) |
| `selfdrive/modeld/models/driving_supercombo_metadata.pkl` | Output slices + input shapes (the loader reads this) |
| `selfdrive/modeld/runners/driving_supercombo_rknn.py` | The RKNN runtime runner (feeds inputs, slices output) |

---

## 8. Troubleshooting (obstacles we hit + how they were solved)

| Error | Cause | Fix |
|-------|-------|-----|
| `pip install rknn-toolkit2` fails on macOS | Not supported on macOS | Use Docker (§3) |
| x86 wheel install → process becomes `<defunct>` | Rosetta can't run RKNN's native C++ | Use the **aarch64** wheel (§3) |
| `cmake` missing, onnxoptimizer build fails | Transitive dep builds from source | `apt-get install cmake build-essential` |
| `Invalid quantized_dtype 'fp'` | Wrong dtype string | Use `w16a16i` (§4) |
| `quantized_dtype = 'w8a16' not support in 'rk3588'` | w8a16 is RKLLM-only | Use `w16a16i` (§4) |
| `Unsupport onnx opset 20, need <= 19` | Model ships at opset 20 | Run `rewrite_gelu_for_opset19.py` (§2 step 4) |
| `No Previous Version of Gelu exists` | onnx version_converter can't downgrade Gelu | The rewriter replaces Gelu with erf first, then downconverts |
| `Not Support Dtype: 2` | UINT8 inputs rejected by w16a16i | Run `cast_uint8_inputs_to_float.py` (§2 step 3) |
| Output files written to wrong path (outside Docker mount) | `Path(__file__).parents[2]` resolves to `/` in container | Set `REPO_ROOT_OVERRIDE=/work` env var |
| Files >100 MB rejected by GitLab push | GitLab file-size limit | Track via git-lfs (see `.gitattributes`) |

---

## 9. INT8 conversion recipe (when ready to try)

If/when we decide to test INT8 for speed, here's the recipe:

1. **Curate calibration images** — pick ~500 good driving frames from
   `tools-local/calibration_frames/extracted/` (day + night mix, both cameras). Save the
   selected images to `tools-local/calibration_frames/calibration_dataset/`.

2. **Prepare the dataset file** — RKNN needs a `.txt` listing image paths (or a function
   that yields numpy arrays). Create `dataset.txt` with one path per line.

3. **Modify the conversion script:**
   ```python
   # In config():
   rknn.config(
       mean_values=[[]], std_values=[[]],
       target_platform='rk3588',
       quantized_dtype='w8a8',           # ← changed from w16a16i
       quantized_method='channel',
       quantized_algorithm='mmse',       # ← add: better scale selection
       optimization_level=3,
   )
   # In build():
   rknn.build(
       do_quantization=True,             # ← changed from False
       dataset='tools-local/calibration_frames/dataset.txt',
   )
   ```

4. **Run in Docker** (same as FP16 conversion — §3). The build will take longer because
   it runs each calibration image through the model to measure activations.

5. **Benchmark on device** — push the new `.rknn`, measure inference time. If it hits
   ~66ms, the speedup worked. Then test-drive for accuracy.

6. **If accuracy is bad** — try `auto_hybrid=True` in `build()` to keep sensitive layers
   in FP16. Or abandon INT8 and stay on FP16 0.10.

---

## 10. Findings log (what we tried, what worked, what didn't)

| Date | What we tried | Result | Verdict |
|------|--------------|--------|---------|
| 2026-08-04 | FP16 conversion (`do_quantization=False`) | Model works, 132ms/7.5Hz | ✅ Valid but too slow for 20Hz |
| 2026-08-07 | NPU core mask `0x4` → `0_1_2` | 142ms → 138ms (noise) | ❌ Core mask is scheduling affinity, not parallelism |
| 2026-08-07 | Verify supercombo output validity | 2580/2580 finite with correct NCHW | ✅ Model is numerically sound (earlier `inf` was benchmark bug) |
| 2026-08-07 | Calibration frame extraction from device | 879 frames from both cameras | ✅ Ready for curation |
| 2026-08-07 | Downloaded 2026-08-03 route for calibration | ALL PARKED (0.0 m/s) — 8 segments wasted | ❌ Lesson: check qlog speed BEFORE downloading HEVC |
| 2026-08-07 | Built `check_route_speed.py` to verify driving via qlog vEgo | Works: correctly identified parked vs driving | ✅ Use this before all future downloads |
| 2026-08-07 | Brightness comparison vs comma devices | Our ecamera matches comma; fcamera darker | ℹ️ Raw frames are supposed to look dark |
| 2026-08-07 | INT8 conversion (w8a8, normal algo, 217 samples) | 71MB model, output valid | ✅ Conversion works |
| 2026-08-07 | INT8 Python benchmark (rknnlite) | **107ms / 9.3Hz** — 1.23× faster than FP16 | ⚠️ Python adds overhead, not production number |
| 2026-08-07 | INT8 C API benchmark (production-equivalent) | **79.5ms / 12.6Hz** — 1.27× faster than FP16 est. | ❌ Still below 20Hz, not viable for production driving |
| 2026-08-07 | 0.10 split C API benchmark (validation) | **32.8ms / 30.5Hz** (vision 29.7ms + policy 3.1ms) | ✅ C API is 2× faster than Python, confirms methodology |
| 2026-08-07 | 0.10 vision INT8 conversion attempt | RKNN 2.3.2 quantizer crashes (`zero-size array`) | ❌ Toolkit bug, not fixable without downgrading to 2.3.0. Doesn't matter — 0.10 FP16 at 30.5Hz already has huge headroom |

---

## 11. How to benchmark a model on the device

There are **two ways** to measure inference speed. Always use the C API for production
numbers — Python adds 30-100% overhead.

### Method A: C API benchmark (production-equivalent, RECOMMENDED)

This uses the same RKNN C API calls as production modeld. Gives the real number.

**The benchmark tool:** `tools-local/bench_rknn.c`

**Step 1 — Connect to the device**

If using the **MCP kommu-ssh server** (the only way if your shell is sandboxed):
```
# MCP bridge must be running on the Mac (started from terminal, not sandboxed):
# SSH_HOST=192.168.0.9 SSH_USER=kommu SSH_KEY=/Users/<you>/lutfime-GitHub \
#   MCP_PORT=8787 nohup node ~/.zcode/mcp-ssh/server.mjs &
#
# Then all device interaction goes through MCP ssh_exec/ssh_get/ssh_put tools.
# The Mac's direct SSH/SCP is BLOCKED by the sandbox — must use MCP.
```

If you have **direct SSH access** (not sandboxed):
```bash
SSH="ssh -i /Users/<you>/lutfime-GitHub kommu@192.168.0.9"
SCP="scp -i /Users/<you>/lutfime-GitHub"
```

**Step 2 — Push and compile the benchmark on device**
```bash
# Push the C source (via MCP ssh_put or SCP):
scp -i ~/.ssh/yourkey tools-local/bench_rknn.c kommu@192.168.0.9:/tmp/

# Compile on device:
ssh kommu@192.168.0.9 'gcc -O2 -o /tmp/bench_rknn /tmp/bench_rknn.c \
  -I/data/openpilot/third_party/rknpu/include \
  -L/usr/lib -lrknnrt'
```

**Step 3 — Benchmark**
```bash
# Benchmark any .rknn model (20 runs):
ssh kommu@192.168.0.9 '/tmp/bench_rknn /data/openpilot/selfdrive/modeld/models/driving_vision.rknn 20'
ssh kommu@192.168.0.9 '/tmp/bench_rknn /data/openpilot/selfdrive/modeld/models/driving_policy.rknn 20'
ssh kommu@192.168.0.9 '/tmp/bench_rknn /tmp/your_new_model.rknn 20'
```

Output shows: mean, median, min, max in ms, plus Hz rate and output validity check.

### Method B: Python rknnlite benchmark (quick sanity check)

Faster to write but adds 30-100% overhead. Use only for quick validation, never for
production decisions.

```python
# On device (/usr/local/venv/bin/python has numpy + rknnlite):
from rknnlite.api import RKNNLite
import numpy as np, time

rknn = RKNNLite()
rknn.load_rknn("/path/to/model.rknn")
rknn.init_runtime(core_mask=RKNNLite.NPU_CORE_2)

inputs = [np.zeros((1,12,128,256), dtype=np.float32)]  # adjust to model inputs
for _ in range(3): rknn.inference(inputs=inputs)  # warmup

times = []
for _ in range(20):
    t0 = time.time()
    rknn.inference(inputs=inputs)
    times.append((time.time()-t0)*1000)

print(f"Mean: {np.mean(times):.1f}ms")
```

### Transferring model files to/from the device

**Downloading from device (device → Mac):**
- Use MCP `ssh_get` for small files (<1MB)
- For large files (HEVC, models): use the **chunked base64 downloader** at `/tmp/mcp_chunk_dl.py`
  (8MB chunks, base64-encoded, MD5-verified). See `calib_dataset.py` for usage.

**Uploading to device (Mac → device):**
- `ssh_put` works for files <30KB only (shell argument limit)
- For large files: **start a temporary HTTP server on the Mac**, then have the device
  `curl`/`wget` from it:
  ```bash
  # Mac side (must NOT be sandboxed — use terminal, not MCP):
  cd /path/to/models && python3 -m http.server 9876 --bind <mac_ip> &

  # Device side (via MCP ssh_exec):
  # curl -s -o /tmp/model.rknn http://<mac_ip>:9876/model.rknn

  # IMPORTANT: HTTP download + SSH can saturate the network and cause SSH timeouts.
  # Start the download in the background, then poll separately:
  # nohup wget -q -O /tmp/model.rknn http://<mac_ip>:9876/model.rknn &
  # Then check: ls -lh /tmp/model.rknn
  ```

### Reading production model execution time

The real production number comes from modeld's `modelV2.modelExecutionTime` published via
the cereal messaging system. To read it:
```bash
# On device, if modeld is running (ignition on):
# Check the rlog for modelV2 messages
/usr/local/venv/bin/python3 -c "
import cereal.messaging as messaging
sm = messaging.SubMaster(['modelV2'])
while True:
    sm.update()
    if sm.updated['modelV2']:
        print(f\"execTime: {sm['modelV2'].modelExecutionTime:.1f}ms\")
        break
"
```
Or replay a route log and check `modelV2.modelExecutionTime` in the rlog data.

---

## 12. Converting the 0.10 split models (vision + policy)

The 0.10 split models (`driving_vision.onnx` + `driving_policy.onnx`) can also be INT8-
converted for comparison. The production models on the device are **confirmed FP16**
(verified: `int8` string appears 0 times in production model, `float16` appears 48 times;
FP16 model is 1.94× the size of INT8 conversion).

### ⚠️ Known issues converting 0.10 vision to INT8

**Issue 1: Zero-size initializer crash**
The 0.10 vision ONNX contains an initializer `pad` with dims `[1, 0]` (zero elements).
RKNN-Toolkit2 2.3.2's quantizer calls `np.min()` on this tensor and crashes:
```
ValueError: zero-size array to reduction operation minimum which has no identity
```
**Fix:** Replace the zero-size initializer with a same-rank tensor filled with zeros
(`[1, 0]` → `[1, 1]` with value 0). See `tools-local/test_010_fix.py` for the fix code.

**Issue 2: auto_hybrid segfault**
Using `auto_hybrid=True` in `rknn.build()` causes a segfault (exit 139) on the 0.10 vision
model during the accuracy adjustment phase. **Do not use auto_hybrid for this model.**
Plain INT8 (no hybrid) works fine.

**Issue 3: In-place ONNX modification**
The fix script modifies the ONNX file. Always work on a **copy** (`driving_vision_fixed.onnx`),
never the original, or you'll need to re-download it each time.

### Conversion result (0.10 vision INT8)

| Model | Size | Notes |
|-------|------|-------|
| 0.10 vision FP16 (production) | 79.4 MB | What's on the device now |
| 0.10 vision INT8 | **40.9 MB** | 48% smaller, conversion succeeded |

The conversion script: `tools-local/test_010_fix.py` (handles zero-size fix + INT8 build).

### Production model dtype verification

To confirm what precision a model is, check the binary for dtype strings:
```python
data = open("model.rknn", "rb").read()
text = data.decode("ascii", errors="ignore")
print(f"float16 count: {text.count('float16')}")  # high = FP16
print(f"int8 count: {text.count('int8')}")          # high = INT8
# FP16 model should be ~2x the size of INT8 (16-bit vs 8-bit weights)
```

Confirmed: production 0.10 models are FP16. `int8` appears 0 times, `float16` appears
48 times (vision) / 8 times (policy).

---

## 13. RKNN Simulator (evaluate without a device)

RKNN-Toolkit2 includes a **simulator** that runs on the Mac in Docker — no device needed.
It can:
- Run model inference (CPU, not NPU — timing is NOT representative of device speed)
- Evaluate per-layer NPU performance estimates (`rknn.eval_perf()`)
- Evaluate memory usage (`rknn.eval_memory()`)
- Compare FP16 vs INT8 output accuracy on the same inputs

**Limitation:** The simulator requires building from ONNX in the same session. You cannot
`load_rknn()` a pre-compiled model and simulate it — you must `load_onnx()` → `build()` →
`init_runtime(target=None)` in one process.

### Simulator workflow

```bash
# In Docker (no device needed):
python3 tools-local/sim_compare.py
```

This builds both FP16 and INT8 from the same ONNX, runs inference on real calibration
frames, and reports the output difference between precisions:
```
Mean abs diff: 0.000123
Max abs diff:  0.004567
Relative diff: 0.034%
Interpretation:
  <0.5%  → imperceptible, safe for driving
  0.5-2% → small drift, test drive needed
  >2%    → significant, likely degrades driving quality
```

**Important:** The simulator's inference time is **CPU time, NOT NPU time**. It tells you
about accuracy and layer composition, not real device speed. Always confirm with the C API
benchmark (§11) on the actual device.

### When to use the simulator vs the device

| Task | Simulator (Docker) | Device (C API) |
|------|-------------------|----------------|
| Check FP16 vs INT8 accuracy diff | ✅ | Can do but slower |
| Estimate per-layer NPU timing | ✅ (rough) | ✅ (real) |
| Measure production inference speed | ❌ | ✅ |
| Detect CPU fallback layers | ✅ | ✅ |
| Test drive the car | ❌ | ✅ |
| No device available | ✅ | ❌ |
