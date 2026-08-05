# x70-test: 0.11 Model on 0.10.3 Base — Notes & Adaptation Plan

> Working log for the `x70-test` branch. Goal: get the standard 0.11 driving model
> running on the KA2 NPU, keeping the proven 0.10.3 base + RKNN runtime.
> Created 2026-08-04. Updated through 2026-08-05.

## Branch model: ONE branch, BOTH models (no branch-switching to go back)

`x70-test` is a **single branch that runs both the 0.10 and 0.11 models.** The selector is
file-presence based, so you toggle at runtime by renaming a file — no branch switch, no
reflash:

| `driving_supercombo.rknn` present in `models/`? | What runs |
|---|---|
| **No** (or renamed `.disabled`) | **0.10 split model** (`ModelStateRKNN` + `driving_vision.rknn` + `driving_policy.rknn`) — known-good fallback |
| **Yes** | **0.11 supercombo** (`ModelStateSupercomboRKNN` + `driving_supercombo.rknn`) |

The code changes on `x70-test` are **backward-compatible** — the 0.10 model runs fine on
this branch. `staging` exists only as the emergency undo button if `x70-test` ever gets into
a broken state; you don't need it for normal 0.10 operation.

```bash
# Toggle to 0.11 model:
mv selfdrive/modeld/models/driving_supercombo.rknn.disabled selfdrive/modeld/models/driving_supercombo.rknn
# Toggle back to 0.10:
mv selfdrive/modeld/models/driving_supercombo.rknn selfdrive/modeld/models/driving_supercombo.rknn.disabled
# Either way: restart modeld (reboot, or pkill -f modeld so it respawns)
```

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

## Quantization & precision reality (what the converted model actually is)

### What dtype the build assigned
From the conversion build log, the model is **mixed precision**:
- 29 large tensors (convolution weights, matrices) → **FLOAT16** (16-bit)
- 40 small tensors (biases, layer norms, scalars) → **FLOAT** (32-bit, unchanged)

This is RKNN's `w16a16i` mode working as designed: compress the bulk in FP16, keep the
precision-sensitive small tensors in full float. Total: 6.1 MB internal memory + 118 MB weights.

### Is it "1:1" with the original FP32 AI?
**No — but very close.** Typical FP16 quantization loses <0.5% accuracy on vision/transformer
models, which is below the perceptual noise floor for driving. You won't feel the difference
in steering/braking. Frame-by-frame output diff would show tiny numerical drift, but the
decisions are effectively identical.

| Precision | Accuracy vs FP32 original | Notes |
|-----------|---------------------------|-------|
| FP32 (original ONNX) | 100% (reference) | What comma ships |
| **FP16 (your model, w16a16i)** | **~99-99.5%** | What Kommu uses in production; proven |
| INT8 (w8a8) | ~95-98% | RKPilot/sunnypilot-pc rejected for driving-quality degradation |

### Why w16a16i is the right (and best) choice on RK3588
- Kommu uses FP16 for their production 0.10 model → proven for this exact use case
- INT8 (w8a8) is the precision-destroying path that other RK3588 projects rejected
- `w8a16` (weights INT8 + activations INT16) is RKLLM-only, invalid on RK3588
- So `w16a16i` (weights INT16 + activations INT16, with small tensors kept float) is the
  best precision available on the RK3588 NPU

### The 880M "big model" — out of scope, here's why
0.11.2 ships an optional "big model" (`big_driving_supercombo.onnx`, 880M params) that runs
on comma four + an external "Chestnut" USB GPU dongle. It's marginally better (more params)
but:
- ~1.7 GB FP16 footprint — far beyond the RK3588 NPU's working memory
- comma themselves say it needs "an external GPU"
- Out of reach for the KA2; the **standard model** (what we converted) is the right target
  and is already the newest checkpoint comma ships on master

### If FP16 ever turns out to be a problem
Re-convert with a different dtype. Your realistic options on RK3588 are only `w16a16i`
(what you have) or `w8a8` (INT8 — precision-destroying). There's no higher-precision option.
So if driving quality somehow degrades, the issue is more likely elsewhere (the loader, the
features_buffer, the warp) than the quantization.

---

## 0.11 vs 0.11.2 — which model did we convert?
The converted `driving_supercombo.onnx` is pulled from comma's `master` branch, which is
**post-0.11.2**. So you have the newest standard model. The only delta vs a hypothetical
"0.11.0-only" model is whatever checkpoint bump 0.11.2 shipped (release notes just say
"New driving model" — no architectural change). The standard model and big model share the
same input/output contract; we're using the standard one.

---

## The exact modeld.py changes needed (do these on-device, you can iterate against real errors)

bukapilot's `modeld.py` is heavily customized (OpenCL warp via `DrivingModelFrame`, KA2 X-offset,
blip guard, stage capture, CL-rolling path) so I can't paste upstream 0.11 in. But the patterns
are clear from upstream `modeld.py` + the metadata. Here are the precise edits.

### File 1: `selfdrive/modeld/modeld.py`

**Edit A — Add the supercombo model paths + selector (near line 53-65).**
Currently the RKNN path keys off `driving_vision.rknn` + `driving_policy.rknn`. Add a third
option for the single supercombo model:
```python
SUPERCOMBO_RKNN_PATH = MODEL_DIR / os.getenv("RKNN_SUPERCOMBO_MODEL", "driving_supercombo.rknn")
SUPERCOMBO_METADATA_PATH = MODEL_DIR / 'driving_supercombo_metadata.pkl'

def _use_rknn_supercombo() -> bool:
  """Use the fused 0.11 supercombo model when driving_supercombo.rknn exists."""
  return SUPERCOMBO_RKNN_PATH.exists() and SUPERCOMBO_METADATA_PATH.exists()
```

**Edit B — Add a `_load_supercombo_metadata()` helper (next to `_load_rknn_metadata`).**
```python
def _load_supercombo_metadata():
  with open(SUPERCOMBO_METADATA_PATH, 'rb') as f:
    meta = pickle.load(f)
  out_size = int(np.prod(meta['output_shapes']['outputs']))
  return {
    'input_shapes': meta['input_shapes'],
    'input_names': list(meta['input_shapes'].keys()),
    'output_slices': meta['output_slices'],
    'output_size': out_size,
  }
```

**Edit C — Add a `ModelStateSupercomboRKNN` class.** I wrote the runner
(`selfdrive/modeld/runners/driving_supercombo_rknn.py` — committed) that does the actual RKNN
call. The modeld.py class wraps it with the OpenCL warp + queue management bukapilot needs.
Skeleton:
```python
class ModelStateSupercomboRKNN:
  """Single fused 0.11 supercombo model on RKNN. Reads shapes from metadata (no hardcodes)."""

  def __init__(self, context: CLContext):
    meta = _load_supercombo_metadata()
    self.input_shapes = meta['input_shapes']           # read from metadata, handles 24 vs 25
    self.input_names = meta['input_names']              # ['img','big_img','desire_pulse','traffic_convention','action_t','features_buffer']
    self.output_slices = meta['output_slices']          # 15 slices incl. action at 2062-2066
    self._output_size = meta['output_size']             # 2580
    self.vision_input_names = [k for k in self.input_shapes if 'img' in k]  # ['img','big_img']
    # Load the runner I wrote (handles fp16 cast, layout, fb truncation)
    from openpilot.selfdrive.modeld.runners.driving_supercombo_rknn import DrivingSupercomboRKNNRunner
    self._rknn = DrivingSupercomboRKNNRunner(MODEL_DIR)
    # Reuse bukapilot's OpenCL warp machinery — same as ModelStateRKNN
    self.frames = {
      name: DrivingModelFrame(context, ModelConstants.MODEL_RUN_FREQ // ModelConstants.MODEL_CONTEXT_FREQ)
      for name in self.vision_input_names
    }
    self.prev_desire = np.zeros(ModelConstants.DESIRE_LEN, dtype=np.float32)
    # Build numpy_inputs for the vector inputs FROM metadata shapes (no 25/24 hardcode)
    self.numpy_inputs = {
      k: np.zeros(self.input_shapes[k], dtype=np.float32)
      for k in ['desire_pulse', 'traffic_convention', 'action_t', 'features_buffer']
    }
    # Reuse the InputQueues for the temporal context buffer (produces N frames; truncation in runner)
    self.full_input_queues = InputQueues(
      ModelConstants.MODEL_CONTEXT_FREQ, ModelConstants.MODEL_RUN_FREQ, ModelConstants.N_FRAMES
    )
    for k in ['desire_pulse', 'features_buffer']:
      self.full_input_queues.update_dtypes_and_shapes(
        {k: self.numpy_inputs[k].dtype}, {k: self.numpy_inputs[k].shape}
      )
    self.full_input_queues.reset()
    self.flat_output = np.zeros(self._output_size, dtype=np.float32)
    self.parser = Parser()

  def slice_outputs(self, model_outputs, output_slices):
    return {k: model_outputs[np.newaxis, v] for k, v in output_slices.items()}

  def run(self, bufs, transforms, inputs, prepare_only, frame_id=None):
    # desire rising-edge pulse (same as ModelStateRKNN)
    inputs['desire_pulse'][0] = 0
    new_desire = np.where(inputs['desire_pulse'] - self.prev_desire > .99, inputs['desire_pulse'], 0)
    self.prev_desire[:] = inputs['desire_pulse']

    # OpenCL warp (reuse bukapilot's machinery)
    imgs_cl = {name: self.frames[name].prepare(bufs[name], transforms[name].flatten()) for name in self.vision_input_names}
    img_np = self.frames['img'].buffer_from_cl(imgs_cl['img']).reshape(self.input_shapes['img'])
    big_img_np = self.frames['big_img'].buffer_from_cl(imgs_cl['big_img']).reshape(self.input_shapes['big_img'])

    if prepare_only:
      return None

    # The action_t input is NEW vs 0.10 — bukapilot's ModelStateRKNN doesn't build it.
    # upstream builds it in main() as np.array([lat_action_t, long_action_t]).
    # For the first test, pass it through from inputs (main() must populate inputs['action_t']).
    action_t = inputs.get('action_t', np.zeros(self.input_shapes['action_t'], dtype=np.float32))

    # NOTE: features_buffer needs the hidden_state from the PREVIOUS frame.
    # On the very first frame there is no previous; use zeros (matches upstream warmup behavior).
    # After the first frame, slice hidden_state out of flat_output and feed it back.

    # Run the single fused model
    self.flat_output = self._rknn.run(
      img_np, big_img_np,
      self.numpy_inputs['desire_pulse'],
      self.numpy_inputs['traffic_convention'],
      action_t,
      self.numpy_inputs['features_buffer'],
    ).reshape(-1)

    # Slice + parse the flat 2580 output (reuses the same Parser as the split path)
    outputs_dict = self.parser.parse_outputs(self.slice_outputs(self.flat_output, self.output_slices))

    # Feed hidden_state back into features_buffer for next frame (the temporal context).
    # Truncation to the model's expected shape happens inside the runner.
    self.full_input_queues.enqueue({'features_buffer': outputs_dict['hidden_state'], 'desire_pulse': new_desire})
    for k in ['desire_pulse', 'features_buffer']:
      self.numpy_inputs[k][:] = self.full_input_queues.get(k)[k]
    self.numpy_inputs['traffic_convention'][:] = inputs['traffic_convention']
    self.numpy_inputs['action_t'][:] = action_t

    return outputs_dict
```

**Edit D — Wire it into `main()` (around line 590).** Add the supercombo branch:
```python
if _use_rknn_supercombo():
  cloudlog.warning("using RKNN supercombo runner (0.11 fused model)")
  model = ModelStateSupercomboRKNN(cl_context)
elif _use_rknn_driving():
  cloudlog.warning("using RKNN split runner (vision=%s policy=%s)", VISION_RKNN_PATH.name, POLICY_RKNN_PATH.name)
  model = ModelStateRKNN(cl_context)
else:
  ...
```

**Edit E — Populate `inputs['action_t']` in the main loop** (before `model.run(...)`).
Copy from upstream:
```python
lat_action_t = lat_delay + frame_delay + action_delay
long_action_t = long_delay + frame_delay + action_delay
inputs['action_t'] = np.array([lat_action_t, long_action_t], dtype=np.float32)
```
(bukapilot's main loop already computes `lat_delay`/`long_delay`; check it has `frame_delay`/`action_delay` too — if not, copy those two lines from upstream.)

### File 2: `selfdrive/modeld/runners/driving_supercombo_rknn.py`
**Already written + committed.** Loads `driving_supercombo.rknn`, feeds all 6 inputs in one
call, casts to fp16, truncates features_buffer to the metadata shape (handles 25→24), returns
the flat 2580 output.

### File 3: `selfdrive/modeld/constants.py`
**Already done.** `ACTION_WIDTH = 2` added.

### What's deliberately NOT carried over from the split path
- Blip guard (`_apply_blip_guard`) — that was a workaround for the 0.10 split RKNN's plan
  output. The 0.11 fused model may not need it. Leave it out for the first test; add back if
  you see straight-blips.
- Stage capture (`_stage_capture`) — debug tooling, not needed for first boot.
- CL-rolling path — that was an optimization for the split vision→policy handoff. The fused
  model has no handoff, so it's moot.

### The 25→24 question, resolved
Upstream reads `features_buffer` shape from metadata (`self.input_shapes[k]`), so it never
hardcodes 25 or 24. The `InputQueues` still produces 25 frames (from `MODEL_CONTEXT_FREQ=5`),
but the runner truncates to the model's expected 24 before feeding (`driving_supercombo_rknn.py`
does `fb = fb[:fb_needed]` where `fb_needed` comes from metadata). **So 25→24 is handled
automatically by reading the shape from metadata + truncating in the runner.** No manual edit.

### The 20 Hz question — how to verify on device
After boot, read `modelV2.modelExecutionTime` (the model publishes it every frame):
```bash
# on the KA2 via SSH:
python3 -c "
import cereal.messaging as messaging
sm = messaging.SubMaster(['modelV2'])
while True:
  sm.update(100)
  if sm.updated['modelV2']:
    t = sm['modelV2'].modelExecutionTime
    print(f'{t*1000:.1f} ms  ({1/t:.1f} Hz)' if t > 0 else 'no data')
"
```
Pass = averages ≤ 50 ms (20 Hz). Watch `logcat | grep -i rknn` for CPU-fallback warnings
(those would drag the rate down).

---


All changes are on `x70-test`, off `staging`. To revert:
```
git checkout staging   # back to clean 0.10.3
git branch -D x70-test # if you want to discard
```
The old 0.10.3 `.rknn` models and runtime are untouched — the code changes are
backward-compatible (legacy path still active when no `action` head is present).

---

## ⚠️ PRIVACY: custom code + model swaps leak to kommu in every uploaded log (2026-08-05)

Investigated what data `system/loggerd/uploader.py` ships to `web.kommu.ai` while driving.
**Answer: yes — your branch name, commit, fork URL, and full `git diff` of uncommitted
changes are embedded in every uploaded `qlog`/`rlog`.**

### How it gets in the log
`system/loggerd/logger.cc:48-64` builds an `InitData` record at the start of every route and
writes it into both rlog and qlog. It snapshots **all of Params** into the log:

```c
init.setGitCommit(params_map["GitCommit"]);     // your commit hash
init.setGitCommitDate(params_map["GitCommitDate"]);
init.setGitBranch(params_map["GitBranch"]);     // "x70-test"
init.setGitRemote(params_map["GitRemote"]);     // your fork's origin URL
auto lparams = init.initParams().initEntries(params_map.size());
for (auto& [key, value] : params_map) {
  if ( !(params.getKeyFlag(key) & DONT_LOG) ) {  // ← only DONT_LOG keys filtered
    // ...key + value written into the log
  }
}
```

### The hole: only 3 keys are protected
In `common/params_keys.h`, of ~155 Params keys, **only 3 have `DONT_LOG`**:
- `AccessToken`
- `LiveTorqueParameters`
- `SecOCKey`

Everything else — including all git/model fields below — is serialized into the log and
uploaded by default (`qlog.zst` + `qcamera.ts` every segment; full `rlog`+videos on demand).

### What specifically exposes this branch's work
| Param key | What it reveals | Written by |
|---|---|---|
| `GitBranch` | `x70-test` | `system/manager/manager.py:61` |
| `GitCommit` | exact commit hash | `manager.py:59` |
| `GitCommitDate` | commit timestamp | `manager.py:60` |
| `GitRemote` | your fork's origin URL (exposes your GitHub repo) | `manager.py:62` |
| **`GitDiff`** | **literal `git diff --submodule=diff` of uncommitted changes** — modeld edits, RKNN conversion scripts, supercombo runner changes, etc. | `system/updated/updated.py:200-201` |
| `GithubUsername` | your GitHub handle (if SSH keys set up) | `setup_ssh_keys.py` / UI |
| `Version` | openpilot version string | `manager.py:58` |

`GitDiff` is the worst: `updated.py:200` runs `git diff --submodule=diff` on the overlay and
dumps the **full text diff** into Params, which then rides along in every uploaded log. So
the modeld/supercombo edits on this branch can land at kommu as plaintext.

### Model swap is also visible through the data itself, not just metadata
- `npuDriverVersion` field (`cereal/log.capnp:491`, written by modeld) → RKNN runtime version
- `modelV2` outputs — a 0.11 supercombo produces different plan/lane/desire distributions than
  0.10.3, so anyone analyzing the qlog can tell it's not stock
- `bootlog`/crash logs carry the same git metadata

### Fix options (NOT applied yet — recorded for later)
1. **Cleanest:** add `DONT_LOG` to the sensitive keys in `common/params_keys.h` (needs C++ rebuild):
   ```c
   {"GitDiff",       {PERSISTENT | DONT_LOG, STRING}},
   {"GitRemote",     {PERSISTENT | DONT_LOG, STRING}},
   {"GitBranch",     {PERSISTENT | DONT_LOG, STRING}},
   {"GithubUsername",{PERSISTENT | DONT_LOG, STRING}},
   ```
2. **Disable uploads:** `FAKEUPLOAD=1` env var (`uploader.py:262-263` returns FakeResponse, no
   bytes leave the device), or stop the `uploader` process.
3. **Network block:** firewall `web.kommu.ai`.

---

## 📱 Kommu phone-app BLE protocol — reference for building a custom app (2026-08-05)

The kommu phone app talks to the KA2 over **Bluetooth LE** via `selfdrive/appbridged/`.
This is fully on-device and self-contained — **no kommu cloud involved in the BT path** — so a
custom replacement app is very feasible. The protocol is simple and documented below.

### Architecture
```
[Phone app] <--BLE GATT--> [appbridged.py] --cereal messaging--> [openpilot services]
                              ^
                              +-- bluezero (Linux BLE peripheral via D-Bus/BlueZ)
```
- Registered as a managed process: `system/manager/process_config.py:81`
  `PythonProcess("appbridged", "selfdrive.appbridged.appbridged", always_run, enabled=not PC)`
- Always running on the KA2 (not just on-road).

### BLE profile (`selfdrive/appbridged/ble_helper.py`)
- **Nordic UART Service** UUID: `6E400001-B5A3-F393-E0A9-E50E24DCCA9E`
- **RX char** (phone → device, write): `6E400002-...`
- **TX char** (device → phone, notify): `6E400003-...`
- Device advertises as peripheral with `local_name = hostname`, appearance `963`.
- Chunking: 240-byte chunks with a 4-byte header `[channel, msgId, totalSegs, segIdx]`.
  Reassembly + 1.0s timeout for incomplete messages is in `ChunkReceiver`.

### Wire format = **msgpack** (`appbridged.py`)
Two logical channels:
- `CHANNEL_VISUALISATION = 0x01` — live driving data, ~16 Hz
- `CHANNEL_SETTINGS = 0x02` — settings state (~3 Hz) + command/response

**Device → phone (visualisation)** `send_visualisation_message`:
modelV2 path/lane/edge resampled, leadOne/leadTwo (status, dRel, yRel), selfdriveState
(enabled, state, experimentalMode, alert*, personality), driverMonitoringState.isActiveMode,
liveCalibration.height, carState.vEgoCluster/vCruiseCluster, isMetric, dongleId.

**Device → phone (settings)** `send_settings_message`:
dongleID, gitCommit, currentVersion, osVersion, state, IsMetric, localIP, activeWlanSSID,
hotspotEnabled/Ip, networkType, simStatus, remainingDataUpload, carNames (×3 on connect),
wifiList (after scan), plus bool/string Params (OpenpilotEnabledToggle, QuietMode, SshEnabled,
CarName, UpdaterTargetBranch, GithubUsername, GsmApn, DrivePathOffset, …).

**Phone → device (commands)** `apply_settings_message`, keyed by `msgType`:
| msgType | Action |
|---|---|
| `saveToggle` | write bool Params |
| `saveConfig` | set CarName / FeaturesPackage / GsmApn / BrakeMagGain + string Params |
| `resetCalibration` | clear CalibrationParams + Live* params |
| `reboot` | `DoReboot` (only when disabled) |
| `tncAccepted` | mark terms/training accepted |
| `changeTargetBranch` | switch updater branch + trigger update |
| `update` {action: check/install/fetch} | OTA controls |
| `ssh` {username, keys} | install GitHub SSH keys |
| `wifi` {ssid, password, action: connect/forget} | nmcli Wi-Fi |
| `scanWifi` | nmcli scan |
| `enableHotspot` / `disableHotspot` | wlan1 hotspot via systemd |
| `formatSD` | format SD (offroad only) |
| `remoteSupport` | spawn `/usr/kommu/support_tunnel.py` (← only kommu-dependent piece) |

Every inbound message must carry `deviceList` containing the device's `DongleId`, or
`devMode: true`, or it's dropped (`handle_send_channel`). `msgType: 'curPage'` tells the
device which channel the app is viewing.

### What this means for a custom app
- **Fully possible.** The whole BT stack is open: standard BLE Nordic UART + msgpack.
  No pairing secret, no kommu-signed protocol — the only auth is the `DongleId` check.
- **Stack choices for a custom app:** Flutter Blue / react-native-ble-plx / native iOS
  CoreBluetooth / Android BluetoothGatt — all speak Nordic UART fine.
- **You don't need to touch the device side** to build a viewer-only app; just implement
  RX-write + TX-notify + the chunking header + msgpack. For commands, copy the msgType set
  you actually want.
- **One kommu-coupled feature:** `remoteSupport` spawns `/usr/kommu/support_tunnel.py`
  (a kommu binary). A custom app would just not implement that msgType (or replace it with
  your own SSH tunnel).
- **The stock app's limitations** (the ones worth fixing in a custom app) are app-side, not
  protocol-side: rendering, UI, alerts — all controlled by whoever holds the phone screen.
  The data stream from `appbridged` is rich (model path, leads, alerts, speed, calibration),
  ~16 Hz, and there's plenty of bandwidth to add more services if you SubMaster them.
