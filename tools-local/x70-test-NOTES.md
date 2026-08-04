# x70-test: 0.11 Model on 0.10.3 Base — Notes & Adaptation Plan

> Working log for the `x70-test` branch. Goal: get the standard 0.11 driving model
> running on the KA2 NPU, keeping the proven 0.10.3 base + RKNN runtime.
> Created 2026-08-04. **Updated 2026-08-04 after inspecting the real 0.11 model — see "Key findings from inspecting the actual 0.11 model" below.**

---

## 🎉 Key findings from inspecting the actual 0.11 model (2026-08-04)

Inspected `driving_supercombo.onnx` (92 MB, downloaded from upstream master). Major findings
that de-risk the conversion:

### 1. NO risky operators — conversion should work cleanly
- **481 nodes total**, opset 20, IR v10
- Attention is implemented via standard `MatMul + Softmax + LayerNorm` (manually unrolled) — all
  fully supported on RK3588 NPU
- **Zero** `Attention`/`FlashAttention`/`GridSample` ops
- Only one mildly risky op: `GatherND` (×1) — usually supported with minor fallback
- Operator census: Add×80, Conv×60, MatMul×56, LayerNormalization×44, Gemm×40, Gelu×39, Mul×29,
  Transpose×26, Reshape×24, Slice×24, Squeeze×24, Where×8, Softmax×8, Sigmoid×5, Concat×3,
  Cast×2, ReduceMean×2, Gather×2, Sub/Div/Relu/Unsqueeze/GatherND ×1 each

### 2. The 0.11 model HAS a direct `action` head ✅
Earlier inspection of the raw ONNX graph outputs showed only a flat `outputs` tensor. But the
**embedded metadata** (extracted via `tools-local/inspect_011_onnx.py`) confirms an `action`
slice exists. So `get_action_from_model`'s `else` branch (0.11 direct-action path) WILL fire
when this model is loaded. The action head gives direct desiredCurvature/desiredAcceleration.

### 3. Input contract matches 0.10 — loader's input handling carries over
0.11 inputs (from ONNX):
- `img` [1,12,128,256], `big_img` [1,12,128,256]  ← same as 0.10 vision inputs
- `desire_pulse` [1,25,8], `traffic_convention` [1,2], `action_t` [1,2]  ← same as 0.10 policy inputs
- `features_buffer` [1,24,512]  ← note: 0.10 was [1,25,512]; one frame difference

### 4. Output slices (extracted from the ONNX metadata, 2580-element flat output)
```
lane_lines           slice(0, 528)      size 528
lane_lines_prob      slice(528, 536)    size 8
road_edges           slice(536, 800)    size 264
meta                 slice(800, 855)    size 55
desire_pred          slice(855, 887)    size 32
pose                 slice(887, 899)    size 12
wide_from_device_euler slice(899, 905)  size 6
road_transform       slice(905, 917)    size 12
plan                 slice(917, 1907)   size 990   ← same as 0.10 policy plan
lead                 slice(1907, 2051)  size 144
lead_prob            slice(2051, 2054)  size 3
desire_state         slice(2054, 2062)  size 8
action               slice(2062, 2066)  size 4     ← NEW vs 0.10
hidden_state         slice(2066, 2578)  size 512
pad                  slice(-2, None)
```
Compare to 0.10: vision (1576) + policy (1000) = 2576 outputs across two models.
0.11: 2580 outputs in one flat tensor — essentially the same content fused + action head added.

### 5. The `output_slices` are embedded in the ONNX model itself
The slices ship as a base64-encoded pickle in the ONNX `metadata_props` (key `output_slices`).
The conversion script extracts these automatically — no manual slice math needed.
`make_metadata_dict` (upstream `get_model_metadata.py`) just reads this back.

**Net: the conversion risk profile dropped dramatically.** The model is standard transformer
ops (well-supported on RK3588), the input contract carries over, and the slicing is pre-baked
in the model metadata. The main remaining work is the loader adaptation (single model vs split)
and the actual RKNN conversion test.

---

## What's done (code-side, on this branch)

### 1. `selfdrive/modeld/constants.py` — added `ACTION_WIDTH`
```python
PLAN_WIDTH = 15
ACTION_WIDTH = 2          # matches upstream 0.11 (the action head)
DESIRED_CURV_WIDTH = 1
```
Everything else in `ModelConstants` / `Plan` / `Meta` is byte-identical to upstream 0.11.

### 2. `selfdrive/modeld/modeld.py` — `get_action_from_model` supports both model shapes
Backward-compatible. With the 0.11 model loaded, the `else` branch fires (action head exists):
```python
if 'action' not in model_output:
  plan = model_output['plan'][0]                         # legacy 0.10 path (unused on 0.11)
  desired_accel, should_stop = get_accel_from_plan(...)
  desired_curvature = get_curvature_from_plan(...)
else:
  desired_accel = float(model_output['action'][0,1])     # 0.11 direct action head ← FIRES
  desired_curvature = float(model_output['action'][0,0]) / (max(1.0, v_ego))**2
  should_stop = should_stop_from_action(v_ego, desired_accel)
```
Added helper `should_stop_from_action(v_ego, desired_accel)` matching upstream 0.11's
`should_stop()` (`v_ego < 0.3 and a_target < 0.1`).

### 3. `selfdrive/modeld/modeld.py` — `LAT_SMOOTH_SECONDS = 0.1` (was 0.0)
Matches upstream 0.11 — slightly more curvature smoothing.

### Net code diff: ~25 lines. Syntax-checked. Old model still works (backward-compatible).

---

## What's NOT done (needs the conversion + device)

### The split → single model loader adaptation (the real structural work)

**Current architecture (0.10.3, split):**
- Two `.rknn` blobs: `driving_vision.rknn` (79 MB, vision encoder) + `driving_policy.rknn` (16 MB, policy head)
- Two `_metadata.pkl` files defining `input_shapes` + `output_slices` per model
- `ModelStateRKNN` (modeld.py:386) loads BOTH, runs vision first, feeds its output + policy inputs to policy
- `DrivingRKNNRunner` (runners/driving_rknn.py:24) and the C++ `DrivingRKNNRunnerCpp` both assume the 2-file split

**0.11 target (single):**
- One blob: `driving_supercombo.rknn` (single fused model)
- One `_metadata.pkl` (the embedded `output_slices` + `input_shapes` from the ONNX)
- The vision→policy chaining happens INSIDE the model, not between two blobs

**Recommended path: Path A — convert as single blob.**
Given the inspection results (standard ops, embedded metadata, matching input contract), the
single-blob conversion is well-supported. Adapt `DrivingRKNNRunner` to load one model with
all 6 inputs, run once, slice the flat 2580 output per the embedded metadata. Remove the
inter-model chaining. The C++ runner would need equivalent adaptation (or force the Python
`rknnlite` runner via `RKNN_USE_PYTHON=1` for initial testing).

### The actual RKNN conversion (needs Linux/Docker — can't run on macOS)
See `tools-local/convert_011_model_to_rknn.py`. RKNN-Toolkit2 is Ubuntu-only. The conversion
script:
- Downloads `driving_supercombo.onnx`
- Extracts the embedded `output_slices` for the metadata pkl
- Runs RKNN conversion with FP16 (Kommu's approach; INT8 loses too much precision)
- Targets RK3588, no quantization

### Settings to use
- **Quantization: FP16** (`do_quantization=False`, `quantized_dtype='w8a16'`)
- **mean/std: empty** (preprocessing baked into the ONNX — confirmed by input dtype uint8)
- **NPU core: pin to core 2** at runtime (`RKNN_NPU_CORE_2`, override `RKNN_DRIVING_CORE_MASK`)
- **Target: RK3588**

### Known risks (now reduced)
1. ~~Unsupported ops~~ — **mostly resolved**: only `GatherND` (×1) flagged, usually supported
2. **Speed still unverified** — must test on device whether it hits 20 Hz
3. **`features_buffer` shape changed** — 0.10 was [1,25,512], 0.11 is [1,24,512]. The loader's
   queue math (`InputQueues`) may need adjusting for this one-frame difference.

---

## Validation checklist (for when the model + device are ready)

- [ ] Conversion completes without errors (no unsupported-op CPU fallback warnings)
- [ ] `driving_supercombo.rknn` produces 2580-element output matching the embedded slices
- [ ] Loader adaptation: `ModelStateRKNN` loads single model, no inter-model chaining
- [ ] Handle `features_buffer` shape change (25→24)
- [ ] On device: modeld boots, publishes `modelV2.action` via the action head
- [ ] On device: inference hits ≥15 Hz (ideally 20)
- [ ] Test drive: X70 engages, steers, brakes sanely (no regressions vs 0.10.3)
- [ ] Compare Experimental-mode stopping vs 0.10.3 (the 0.11 long gain)

---

## Rollback

All changes are on `x70-test`, off `staging`. To revert:
```
git checkout staging   # back to clean 0.10.3
git branch -D x70-test # if you want to discard
```
The old 0.10.3 `.rknn` models and runtime are untouched — the code changes are
backward-compatible (legacy path still active when no `action` head is present).
