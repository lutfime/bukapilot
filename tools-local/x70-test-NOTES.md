# x70-test: 0.11 Model on 0.10.3 Base — Notes & Adaptation Plan

> Working log for the `x70-test` branch. Goal: get the standard 0.11 driving model
> running on the KA2 NPU, keeping the proven 0.10.3 base + RKNN runtime.
> Created 2026-08-04.

---

## What's done (code-side, on this branch)

### 1. `selfdrive/modeld/constants.py` — added `ACTION_WIDTH`
```python
PLAN_WIDTH = 15
ACTION_WIDTH = 2          # NEW (matches upstream 0.11; size of the action head)
DESIRED_CURV_WIDTH = 1
```
Everything else in `ModelConstants` / `Plan` / `Meta` is byte-identical to upstream 0.11.

### 2. `selfdrive/modeld/modeld.py` — `get_action_from_model` now supports both model shapes
Backward-compatible: if the model has no `action` head (your current 0.10 `.rknn`), it
derives from `plan` (unchanged behavior). If the model HAS an `action` head (0.11), it reads
directly:
```python
if 'action' not in model_output:
  plan = model_output['plan'][0]                         # legacy 0.10 path
  desired_accel, should_stop = get_accel_from_plan(...)
  desired_curvature = get_curvature_from_plan(...)
else:
  desired_accel = float(model_output['action'][0,1])     # 0.11 direct action head
  desired_curvature = float(model_output['action'][0,0]) / (max(1.0, v_ego))**2
  should_stop = should_stop_from_action(v_ego, desired_accel)
```
Added helper `should_stop_from_action(v_ego, desired_accel)` matching upstream 0.11's
`should_stop()` (`v_ego < 0.3 and a_target < 0.1`).

### 3. `selfdrive/modeld/modeld.py` — `LAT_SMOOTH_SECONDS = 0.1` (was 0.0)
Matches upstream 0.11 — slightly more curvature smoothing. Optional/reversible.

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
- One `_metadata.pkl`
- The vision→policy chaining happens INSIDE the model, not between two blobs

**Two paths to handle this:**

**Path A — Convert as single blob (matches upstream 0.11):**
- Convert `driving_supercombo.onnx` → one `driving_supercombo.rknn`
- Adapt `DrivingRKNNRunner` / `DrivingRKNNRunnerCpp` to load one model instead of two
- Remove the inter-model chaining logic in `ModelStateRKNN`
- Cleaner long-term, more code change up front

**Path B — Keep the split (matches your existing architecture):**
- Split `driving_supercombo.onnx` at the vision/policy boundary yourself
- Convert each half → `driving_vision.rknn` + `driving_policy.rknn` (same names as today)
- Minimal runner code change (the loader already handles two files)
- Requires knowing where the split boundary is in the 0.11 model (the ONNX graph)
- Risk: 0.11's fused architecture may not have a clean split point

**Recommended: try Path A first.** If RKNN-Toolkit2 can convert the single model cleanly
and it fits on the NPU, the loader adaptation is well-contained (one class, two functions).
Path B is the fallback if Path A hits memory/op issues.

---

## The ONNX → RKNN conversion (needs Linux; can't run on macOS)

See `tools-local/convert_011_model_to_rknn.py` for the scaffolded script with documented
FP16 settings. RKNN-Toolkit2 runs only on Ubuntu (18.04/20.04/22.04/24.04), so this runs
in Docker or on a Linux box.

### Settings to use (best-guess from inspecting Kommu's runtime + RKPilot's failures)
- **Quantization: FP16** (`do_quantization=False`, `quantized_dtype='w8a16'` or force fp16)
  - RKPilot/sunnypilot-pc rejected RKNN because default INT8 conversion lost too much precision
  - Kommu's runtime explicitly casts inputs to float16 (`runners/driving_rknn.py:19`)
  - FP16 keeps near-full precision for vision models (<1-2% loss typically)
- **mean/std normalization: copy from the .onnx model's preprocessing** (TBD — inspect the ONNX input node)
- **NPU core: pin to core 2** (Kommu's default: `RKNN_NPU_CORE_2`, override via `RKNN_DRIVING_CORE_MASK`)
- **Target: RK3588** (`rknn.config(target_platform='rk3588')`)

### Known risks
1. **Unsupported ops → CPU fallback.** If RKNN-Toolkit2 doesn't support an op the 0.11 model
   uses (e.g. Flash Attention — NOT supported on RK3588, only RK3562/RK3576), it falls back
   to CPU and kills perf. Watch for warnings during conversion.
2. **Memory: 880M "big model" is out of scope.** Target the standard `driving_supercombo.onnx`
   only. The big model has ~1.7 GB FP16 footprint + attention-matrix-vs-32KB-SRAM risk.
3. **Speed unverified.** Even if conversion succeeds, whether it hits 20 Hz on the NPU is
   unknown until tested on the KA2.

---

## Validation checklist (for when the model + device are ready)

- [ ] Conversion completes without errors (no unsupported-op CPU fallback warnings)
- [ ] `driving_supercombo.rknn` produces same output shapes as `driving_vision.rknn` + `driving_policy.rknn`
- [ ] Loader adaptation: `ModelStateRKNN` loads single model, no inter-model chaining
- [ ] On device: modeld boots, publishes `modelV2.action` with sane values
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
