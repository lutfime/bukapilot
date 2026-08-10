# Community Driving Models Research & Integration

> Compiled 2026-08-11. Documents the full investigation, conversion, and runtime
> integration of community driving models (OPM10V3, WMI v12) for bukapilot on
> the Kommu KA2 (RK3588 NPU). Supersedes assumptions in earlier docs.

---

## 0. TL;DR — what we built

| Model | Architecture | Est device speed | Status |
|---|---|---|---|
| **Default (0.10.3)** | 2-file split (vision + policy) | 32.8ms / 30.5 Hz | ✅ Production |
| **WMI V12** | 2-file split (community finetune) | ~31ms / ~32 Hz | ✅ Converted + selector |
| **OPM10V3** | 3-file split (vision + on_policy + off_policy) | ~32ms / ~31 Hz | ✅ Converted + selector |
| 0.11 supercombo | fused (rejected) | 79.5ms / 12.6 Hz | ❌ Too slow, toggle removed |

All three viable models estimated ~30 Hz. The app now has a **model selector** (not a toggle)
where the user picks between Default / WMI V12 / OPM10V3.

---

## 1. The community model ecosystem

Beyond comma's stock models, there's a large community ecosystem of driving models.

**Database:** [Sunnylink Wiki](https://sunnylink.wiki/models) — 80+ models with comparison.
**Weekly reviews:** [sunnypilot community Weekly Models TL;DR](https://community.sunnypilot.ai/t/weekly-models-tl-dr/1509)
**Forks:** [FrogPilot](https://github.com/FrogAi/FrogPilot), [sunnypilot](https://github.com/sunnypilot/sunnypilot)

### Popular models (from Reddit r/Comma_ai + sunnypilot community)

| Model | Type | Notes |
|---|---|---|
| **WMI V12** | 2-file split (0.10 base) | "World Model Inference" — community-recommended for 0.10.3. Recommended alongside CD210 and North Nevada v2. |
| **OPM10V3** | 3-file split (post-0.11) | "OP Model 10 V3" — strong longitudinal control, praised for acceleration. Lateral has right-bias + wobble. |
| **Down to Ride v6** | 2-file split | "Wife-approved" comfort tuning. Good curve handling. |
| **Tomb Raider 16** | 2-file split | Strong lateral performer per community. |
| **Macrostiff** | single fused | "Best lateral model since TR16". |

### Critical finding: architecture varies by base version

**NOT all community models are supercombo.** The architecture depends on which openpilot
version the model was trained against:

| Base version | Architecture | Files |
|---|---|---|
| 0.9.x | supercombo (single fused) | `supercombo.onnx` |
| **0.10.x** | **split** | `driving_vision.onnx` + `driving_policy.onnx` |
| post-0.11 | **3-split** | `driving_vision.onnx` + `driving_on_policy.onnx` + `driving_off_policy.onnx` |

Earlier docs assumed all community models were supercombo-class. **This was wrong.**
The sunnypilot model manifest confirms all 3 architectures exist in the catalog.

---

## 2. How sunnypilot distributes models

### Manifest

`https://raw.githubusercontent.com/sunnypilot/sunnypilot-models/gh-pages/docs/driving_models_v18.json`

74 bundles. Each bundle has:
- `ref` — commaai/openpilot commit hash (determines architecture)
- `models[].artifact.download_uri.url` — GitLab download URL (chunked)
- `models[].artifact.file_name` — always a single tinygrad `.pkl` (even for split models)

### Distribution format: compiled tinygrad CapturedJit

The `.pkl` files are **NOT** portable model weights — they're pre-compiled tinygrad JIT
closures with OpenCL C kernels baked in (targeting QCOM Snapdragon GPU). A 120 MB pkl
contains 94 OpenCL kernels + 445 weight blobs + the UOp IR graph.

**You cannot convert pkl → RKNN.** You need the ONNX source files.

### Getting the ONNX source

sunnypilot's CI pipeline (`.github/workflows/sunnypilot-build-model.yaml`):
1. `git checkout commaai/openpilot` at the model's `ref` commit
2. `git lfs pull` — resolves the ONNX files from LFS
3. Uploads `*.onnx` as CI artifact
4. On a TICI runner: compiles ONNX → tinygrad JIT pkl via `compile_modeld.py`

**The LFS trick:** GitHub's LFS batch API (`/info/lfs/objects/batch`) returns 404 for
some objects, but `git lfs smudge` (the checkout filter) resolves them through a different
code path. This is how we download the ONNX:

```bash
git clone --no-checkout --filter=blob:none https://github.com/commaai/openpilot.git
cd openpilot
git fetch origin <commit_hash>
git checkout FETCH_HEAD -- selfdrive/modeld/models/driving_*.onnx
cat selfdrive/modeld/models/driving_vision.onnx | git lfs smudge > driving_vision.onnx
```

---

## 3. Architecture details of converted models

### OPM10V3 (3-file split)

```
Commit: 7d4c295c27bc3f72727c49ec73b98e45745b588d (2026-04-19)
Category: "2026 World Models"

                ┌─────────────┐
  img+big_img → │   vision    │ → 632 features (hidden_state 512-d)
                │  (176 nodes)│   LEANER than 0.10.3 (which has 1576 out)
                └──────┬──────┘
                       │ hidden_state → features_buffer (shared)
                ┌──────┴──────┐
                ↓             ↓
       ┌────────────┐  ┌────────────┐
       │ on_policy  │  │ off_policy │
       │ (77 nodes) │  │ (110 nodes)│
       │ plan,      │  │ plan,      │
       │ desire_    │  │ lane_lines,│
       │ state      │  │ lead,      │
       │            │  │ road_edges │
       └────────────┘  └────────────┘
```

**Vision checkpoint:** `8c97c159-21d3-4088-8507-7e7cf721998e` (newer than 0.10.3's `6a7d09ad`)
**Policy checkpoint:** `1cc30203-aa24-4175-9a55-2c841b49ea20/100`
**Opset:** 20 (needs Gelu→erf rewrite, 68 Gelu nodes across 3 files)

### WMI V12 (2-file split)

```
Commit: 7e3fd7a63c5a09c3fabe108b1b62bba3cf684878 (2026-01-13)
Category: "Master Models"

  img+big_img → vision (1576 out, SAME as 0.10.3) → hidden_state
                                                        ↓
                                                   policy (1000 out)
                                                   plan + desire_state
```

**Vision checkpoint:** `6a7d09ad-bcc9-43bc-916d-29287e60cee2/200` — **IDENTICAL to 0.10.3**
**Policy checkpoint:** `a27b3122-733e-4a65-938b-acfebebbe5e8/100` — same checkpoint ID but weights differ (finetuned)
**Opset:** 17 (no Gelu rewrite needed — easiest conversion)

---

## 4. Conversion pipeline

### For 2-file split models (WMI V12, DTR v6, Tomb Raider, etc.)

Uses the existing `cast_uint8_inputs_to_float.py` + standard RKNN convert. Opset 17, no Gelu rewrite.

Script: `tools-local/convert_and_bench_wmiv12.py` (convert + benchmark in one Docker pass)

### For 3-file split models (OPM10V3)

Uses `cast_uint8_inputs_to_float.py` + `rewrite_gelu_for_opset19.py` + RKNN convert.

Script: `tools-local/convert_opm10v3_to_rknn.py`
Docker wrapper: `tools-local/run_opm10v3_conversion.sh`

### Pristine ONNX backups

```
tools-local/models_store/
├── opm10v3-onnx-original/     ← opset 20, pre-conversion (sha256 verified)
│   ├── driving_vision.onnx    (23 MB)
│   ├── driving_on_policy.onnx (13 MB)
│   └── driving_off_policy.onnx (13 MB)
└── wmiv12-onnx-original/      ← opset 17 originals
    ├── driving_vision.onnx    (44 MB)
    └── driving_policy.onnx    (13 MB)
```

### Converted RKNN files (gitignored)

```
selfdrive/modeld/models_dev/
├── opm10v3/
│   ├── driving_vision_opm10v3.rknn          (49 MB)
│   ├── driving_on_policy_opm10v3.rknn       (15 MB)
│   ├── driving_off_policy_opm10v3.rknn      (21 MB)
│   └── *_metadata.pkl (3 files)
└── wmiv12/
    ├── driving_vision_wmiv12.rknn           (76 MB)
    ├── driving_policy_wmiv12.rknn           (16 MB)
    └── *_metadata.pkl (2 files)
```

---

## 5. Simulator benchmark methodology

### The simulator CAN run inference (correcting earlier docs)

The RKNN-Toolkit2 simulator (`target=None`) runs inference on CPU. It's not the NPU
production number, but it gives **relative** comparison between models.

**What the simulator CAN do:**
- `load_onnx()` → `build()` → `init_runtime(target=None)` → `inference()` ✅
- Within-model FP16-vs-INT8 comparison ✅
- Output validity checking ✅

**What the simulator CANNOT do:**
- `load_rknn()` + `init_runtime(target=None)` → **ERROR** (must use load_onnx+build instead)
- `eval_perf()` → **ERROR** ("Not support in simulator environment")
- `eval_memory()` → **ERROR** (same)
- Run INT8 inference (CPU has no INT8 units)

### Calibration ratios (from SIMULATOR-RESULTS.md)

| Model type | Sim/Device ratio | Meaning |
|---|---|---|
| Vision-heavy (Conv backbone) | 2.37× | sim is ~2.37× slower than device NPU |
| Policy-heavy (Gemm/MLP) | 1.35× | sim is ~1.35× slower |
| Large fused (supercombo) | 1.08× | sim ≈ device (NPU-bound) |

### Benchmark results

| Model | Sim total | Est device total | Est Hz |
|---|---|---|---|
| 0.10.3 split (reference) | ~70ms | 32.8ms | 30.5 Hz |
| **WMI V12** | 71.4ms | **~31ms** | **~32 Hz** |
| **OPM10V3** | 68.8ms | **~32ms** | **~31 Hz** |
| 0.11 supercombo (rejected) | 79.6ms | 79.5ms | 12.6 Hz |

### Benchmark script

`tools-local/bench_opm10v3.py` — runs load_onnx→build→init_runtime(None)→inference
with 3 warmup + 10 timed runs per model. Prints per-model and total sim time with
calibration-ratio device estimates.

---

## 6. Runtime integration

### Model selector architecture

```
iOS app (Menu picker)
  → sendCommand(["msgType":"saveToggle", "SelectedDrivingModel": "wmiv12"])
  → BLE msgpack to device
  → appbridged.py: set_driving_model("wmiv12") writes /data/params/SelectedDrivingModel
  → modeld reads /data/params/SelectedDrivingModel at startup
  → selects ModelStateRKNN (default/wmi) or ModelState3SplitRKNN (opm10v3)
```

The param is a **string** (`"default"`, `"wmiv12"`, `"opm10v3"`), not a boolean toggle.
This is extensible — adding a new model just means adding a new option + path constants.

### Files modified

| File | Change |
|---|---|
| `selfdrive/modeld/modeld.py` | `_get_selected_driving_model()`, `_use_rknn_3split()`, `_use_wmi_v12()`, `ModelState3SplitRKNN` class, model selection in `main()` |
| `selfdrive/modeld/runners/driving_3split_rknn.py` | **New** — Python rknnlite runner for 3 models |
| `selfdrive/appbridged/appbridged.py` | `get_driving_model()` / `set_driving_model()` + saveToggle handler |
| `kommu-drive/.../SettingsSheet.swift` | `drivingModelSelector` Menu with checkmark |
| `kommu-drive/.../DeviceSettings.swift` | `selectedDrivingModel: String` field |

### What was REMOVED

- `UseSupercomboModel` toggle (iOS + appbridged + modeld) — the 0.11 supercombo is too slow
- `_use_rknn_supercombo()` gate function
- Supercombo branch from `main()` model selection
- `SUPERCOMBO_TOGGLE_FILE` constant

The `ModelStateSupercomboRKNN` class and `driving_supercombo_rknn.py` runner remain as
dead code (not wired in `main()`). They could be revived if needed.

### Output merge logic for 3-split (from sunnypilot source)

sunnypilot's `modeld.py` multi_policy path:
1. Vision runs once → parse vision outputs → extract `hidden_state` → feed to features_buffer
2. Run EACH policy head with the same features_buffer
3. **off_policy's `plan` is dropped** when on_policy also has `plan` (on_policy plan wins)
4. Merge all parsed outputs into one dict

We follow this exact rule in `ModelState3SplitRKNN.run()`.

---

## 7. Performance analysis: why OPM10V3 is fast

The 0.11 supercombo was slow (12.6 Hz) because of **56 attention MatMul layers** (the World
Model transformer). OPM10V3 avoids this bottleneck:

| | 0.11 supercombo | OPM10V3 (3-split) |
|---|---|---|
| Total nodes | 754 | 363 |
| **MatMul (attention)** | **56** | **12** (6 in each policy head, none in vision) |
| Conv | 60 | 60 (same backbone class) |

The vision encoder has **zero attention layers** — pure Conv (FastViT without attention).
Attention only appears in the small policy heads (512-dim features), which are cheap.

---

## 8. Community model reviews (from sunnypilot + Reddit)

### OPM10V3 (OP Model 10 V3)

Tested on: 2018 Corolla Sport, 2023 RAV4, Rivian R1T

**Praise:**
- Longitudinal control is its strength — "accelerates just as well as DEC"
- Best-in-class at 90-degree turns
- Confident stop-to-acceleration

**Complaints:**
- Wobbling on straight highways — "left, right, left, right…"
- Right-bias — crowds the right side of the lane
- Braking anomalies — inconsistent timing
- Turning aggression — clips curbs on longer vehicles

**Note:** bukapilot's `LAT_SMOOTH_SECONDS = 0.2` may already mitigate the wobble complaint.

### WMI V12

Community-recommended alongside CD210 and North Nevada v2 for 0.10.3.
Considered a solid all-around model. The Reddit poster who tried it noted it
"nudged weirdly to the left on highways sometimes."

### DTR v6 (Down to Ride v6)

The model from the original Reddit post. "Wife-approved" — prioritizes gentle
braking and smooth turns. Good curve handling. Could be more centered on highways.
Same 2-file split architecture as 0.10.3, would convert easily.

---

## 9. To benchmark on device

After deploying to the KA2:

```bash
# Python rknnlite benchmark (adds ~30-100% overhead but quick):
python3 tools-local/bench_opm10v3.py

# C API benchmark (production-equivalent):
# See MODEL-CONVERSION-GUIDE.md §11 Method A (bench_rknn.c)
```

The simulator estimates suggest all three models (Default / WMI V12 / OPM10V3) should
hit ~30 Hz on device. The real test is a drive.

---

## 10. Future model additions

To add a new community model to the selector:

1. **Determine architecture** — check the `ref` commit in the manifest, look at model files
2. **Download ONNX** via the LFS smudge trick (§2)
3. **Convert to RKNN** using the appropriate script (2-split or 3-split)
4. **Benchmark** in simulator
5. **Add to selector:**
   - `modeld.py`: add path constants + `_use_xxx()` gate + branch in `main()`
   - `SettingsSheet.swift`: add Menu button + `modelDisplayName` case
   - `DeviceSettings.swift`: no change needed (string field already extensible)

Candidate models for future conversion:
- **Tomb Raider 16** — strong lateral performer, 2-file split
- **Down to Ride v6** — comfort tuning, 2-file split
- **North Nevada v2** — community-recommended, check architecture
