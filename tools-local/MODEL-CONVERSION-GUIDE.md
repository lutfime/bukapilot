# Converting comma's Driving Model to RKNN (for the Kommu KA2 / RK3588 NPU)

> Reusable guide. Follow this whenever comma ships a new driving model checkpoint
> (0.11.x, 0.12, etc.) and you want to run it on the KA2 NPU.
>
> Last successful conversion: openpilot 0.11.2 standard `driving_supercombo.onnx` →
> `driving_supercombo.rknn` (135 MB, w16a16i). Verified 2026-08-04.

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
   `load_onnx` → `build(do_quantization=False)` → `export_rknn`.
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

## 4. Quantization choice: `w16a16i` (and why not the alternatives)

RKNN-Toolkit2 v2.3.2 accepts these `quantized_dtype` values on RK3588:
`w8a8`, `w8a16`, `w16a16i`, `w16a16i_dfp`, `w4a16`.

| Dtype | Precision | Speed | Use? |
|-------|-----------|-------|------|
| `w8a8` (INT8) | ~95-98% | Fastest | ❌ **No** — RKPilot/sunnypilot-pc rejected it; driving quality degrades |
| `w16a16i` (INT16 weights+act) | ~99-99.5% | Fast | ✅ **Yes — what we use** |
| `w8a16`, `w4a16` | Various | Various | ❌ RKLLM-oriented or lower precision |

**Use `w16a16i`.** It's the highest precision available on RK3588 that isn't full float,
and Kommu uses the same FP16-class approach for their production 0.10 model. The converted
0.11 model came out as mixed precision (29 large tensors FLOAT16 + 40 small tensors FLOAT),
~99-99.5% accuracy vs the FP32 original — imperceptible for driving.

> **Bikeshed warning:** several dtype strings you might guess are INVALID:
> - `'fp'` → rejected (`Invalid quantized_dtype`)
> - `'w8a16'` → rejected on RK3588 (`not support in 'rk3588'!`) — it's RKLLM-only
> - `'fp16'` → not a valid value at all
>
> The valid set is exactly: `['w8a8', 'w8a16', 'w16a16i', 'w16a16i_dfp', 'w4a16']`.
> Use `w16a16i`.

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

## 9. Quick reference: the valid dtype strings

For `rknn.config(quantized_dtype=...)` on RK3588 with RKNN-Toolkit2 v2.3.2:

```
w8a8       — INT8 (precision-destroying; avoid for driving)
w8a16      — INVALID on RK3588 (RKLLM-only)
w16a16i    — INT16 weights + activations ← USE THIS
w16a16i_dfp — INT16 variant (dynamic fixed point)
w4a16      — 4-bit weights (low precision)
```

Everything else (`fp`, `fp16`, `i8`, `i16`, `keepFloatAndQuant`) is either invalid or
not a real `quantized_dtype` value. Don't trust web search results that suggest them —
the error message gives the authoritative list.
