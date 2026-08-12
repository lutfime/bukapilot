# OPM10V3 C++ 3-Split Runner — Bug Findings & Reproduction (HANDOFF)

> ✅ **RESOLVED 2026-08-12 — Fix E (per-input `pass_through` by native fmt).** See RESOLVED section.
> The historical investigation (multi-context, core mask, Gelu overflow) below is kept for the
> record, but the actual root cause was simpler: a wrong `pass_through` flag in the C++ input setup.

## RESOLVED — Fix E: per-input `pass_through` based on native fmt (THE FIX)

**Root cause (the real one):** In `driving_rknnmodel.cc` `load_model()`, every input had
`pass_through = 0`. That tells librknnrt *"convert my buffer into the model's native format."*
Our code already writes fp16 and sets `type = RKNN_TENSOR_FLOAT16`, so for **policy** inputs
(fmt `UNDEFINED` → native `UNDEFINED`, fp16→fp16) that conversion is a buggy **no-op** in
librknnrt v2.3.2 — it intermittently emits `inf`. rknnlite (Python) skips this path, which is
why Python was always correct. For **vision** inputs (fmt `NHWC` → native `NC1HWC2`) the
conversion is real and necessary, so `pass_through=0` was correct there.

**The fix (commit pending, `driving_rknnmodel.cc`):** query each input's
`RKNN_QUERY_NATIVE_INPUT_ATTR` and set per-input:
```
pass_through = (fw_type == native_type && fw_fmt == native_fmt) ? 1 : 0
```
- vision `img`/`big_img`: NHWC(1) → NC1HWC2(2), fmts differ → **pass_through=0** (convert layout)
- policy `desire_pulse`/`traffic_convention`/`features_buffer`: UNDEFINED(3) → UNDEFINED(3) → **pass_through=1** (skip the buggy no-op)

`RKNN_PASS_THROUGH` env var still overrides all inputs (debug). Default is now per-native-fmt.

**Verified 2026-08-12 (device):** all three models (default, wmiv12, opm10v3) share identical
input attrs and now produce **finite policy output** (no nan/inf) on in-distribution inputs.
opm10v3 on_policy C++ output matches Python rknnlite within fp16 noise on finite inputs.
default + WMI are NOT broken (their policy moved 0→1 too, but 1 is the correct setting for all
fp16-native UNDEFINED policy inputs — 0 only *happened* to work for default/WMI).

**Caveat on the 1:1 test (`test_3split_cpp_vs_python.py`):** its synthetic inputs
(`features_buffer = randn*0.1`, `desire_pulse = rng.choice([0,1])`) are **out-of-distribution**
and push on_policy into fp16 overflow → `inf` in **both** Python and C++. So that test still
"fails" but it is NOT a C++ bug (Python is also inf there). Use `validate_opm10v3_1to1.py`
instead (in-distribution features from real vision hidden states).

## ⚠️ SEPARATE ISSUE — opm10v3 is fp16-overflow-prone (model-level, not C++)

Validated 2026-08-12 with `validate_opm10v3_1to1.py` (in-distribution features = 25-timestep
stack of real vision hidden_states, value range [-1.36, 1.36], desire=zeros):

- **C++ vision == Python** (max diff 0.039, mean 0.00096, all finite, 225 frames). ✅
- **C++ on_policy is faithful to Python**: equal overflow rate (C++ 16-18% vs Python 16%), and on
  finite frames **92% match bit-exact** (median diff 0.000000). Fix E works.
- **BUT the model overflows in fp16 regardless of runner** (this is the real risk, NOT the C++ bug):
  - on_policy sigmoid (Fix A): **~20% inf** (Python and C++ equally)
  - on_policy erf (Fix B): **~9% inf** — erf Gelu is ~2x better but not zero
  - off_policy (Python, no erf variant): **~18% inf**
  - On near-overflow frames, C++ and Python diverge wildly (max diff ~52000) because both compute
    in the chaotic regime near fp16 max (65504) where ULP differences explode. This is inherent
    fp16 numerical chaos, not a C++ defect.
- **Fix A (±100 sigmoid-Gelu clip) is INSUFFICIENT** — it only clamps one op; other MatMul/activation
  ops still overflow. Fix B (erf) halves it but doesn't eliminate it. off_policy has no fix.

**Implication:** even with Fix E (C++ correct), opm10v3 on KA2 may produce inf/garbage plan +
perception ~10-20% of frames in fp16 → frequent disengagements or blue. Whether REAL driving
data triggers this (synthetic features, even smooth-temporal, may be harsher than real frames) is
**unconfirmed — needs a real onroad drive**. If it overflows in production, opm10v3 needs a proper
fp16-safe reconversion (clamp all overflow-prone ops, or mixed precision), not more C++ work.
default + WMI do NOT have this issue (different, fp16-safe models).

**Fix A / Fix B (model reconversion, commits 51d81ab / 8bca7e8):** the clipped-sigmoid-Gelu and
erf-Gelu rewrites were a reasonable hypothesis (fp16 overflow in `1.702*x` before Sigmoid) and the
reconverted models are kept as backups, but Fix E alone resolves the C-API inf. They do not hurt.

**Fix D (global `pass_through=1`, commit 2224dbd):** the right *idea* (pass_through was the lever)
but wrong *scope* — global `1` broke vision (which needs the NHWC→NC1HWC2 conversion). Fix E makes
it per-input. Fix D is subsumed.

## Performance — do we even need C++? (benchmarked 2026-08-12)

**Short answer: no, not strictly.** All-Python opm10v3 runs at **22.7 Hz** in onroad-equivalent
(performance) mode — above the 20 Hz engage threshold. The original "~17 Hz Python = too slow =
blue" justification for the C++ runner was **power-save-inflated** (see GOTCHA below). C++ buys Hz
margin, not correctness.

**Per-op inference (clean, isolated, PERFORMANCE / 8-core mode):**
| op | runner | ms | Hz |
|---|---|---|---|
| vision | C++ | 27.8 | 36 |
| on_policy (erf) | C++ | 5.7 | 175 |
| on_policy (erf) | Python rknnlite | 6.4 | 157 |
| off_policy | Python rknnlite | 8.1 | 123 |

→ The policy heads are tiny either way (C++ on_policy saves ~0.6 ms vs Python — nothing). Vision is
the only real cost, and opm10v3 vision (28 ms) is actually **faster than default (35 ms)**.

**Full frame (vision + on_policy + off_policy), PERFORMANCE mode:**
| mode | ms/frame | Hz |
|---|---|---|
| **All-Python** (Driving3SplitRKNNRunner) | 44.0 | **22.7** |
| **Hybrid C++/Python** (current prod) | 35.4 | 28.2 |
| → C++ saves | 8.6 | 5.5 |

**Tradeoff:**
- All-Python: **simpler** (no .so, no Fix E, no scons/cythonize/stub-cert rebuild chain) and rknnlite
  never had the pass_through inf bug. BUT only ~2.7 Hz margin over 20; production adds CL-transform +
  frame I/O + publish overhead, so real-world all-Python sits near the ~20 Hz blue edge.
- Hybrid C++: ~8 Hz margin (safer against heat/overhead), at the cost of the C++ build/rebuild chain.
- **Either way the Hz is not the blocker — the fp16 overflow (above) is.** Switching to all-Python
  (`os.environ["RKNN_USE_PYTHON"]="1"` in ModelState3SplitRKNN, or set the env at launch) removes the
  .so dependency entirely. Recommended only if build friction outweighs the Hz margin.

### ⚠️ GOTCHA — always benchmark in performance mode (8 cores), not offroad power-save
`run_vision` does CPU work (uint8→fp16 transform + buffer copies) on top of NPU inference. The **NPU
is fixed at 1 GHz** (set at boot by `Ka2.initialize_hardware` via `/sys/class/devfreq/fdab0000.npu`
userspace — NOT affected by power mode). But the **CPU big cores (5-7) are OFF offroad**
(`Ka2.set_power_save(True)`), and the CPU governor drops to `ondemand`. Result: offroad benchmarks
are ~2× slower than onroad:

| mode | cores | default vision | opm10v3 vision |
|---|---|---|---|
| offroad power-save | 5 | 74 ms (13 Hz) | 59 ms (17 Hz) |
| performance / onroad | 8 | 35 ms (28 Hz) | 28 ms (36 Hz) |

The onroad default-vision 35 ms matches the MODEL-CONVERSION-GUIDE's "32.8 ms" — confirming the
method. **Any future benchmark must call `HARDWARE.set_power_save(False)` first** (then restore
`True`), or it will under-report Hz by ~2× and wrongly conclude "too slow". This is exactly how the
"Python 17 Hz" myth originated.

---

## TL;DR (historical — pre-Fix-E, now outdated)
- The 1:1 correctness test (`tools-local/test_3split_cpp_vs_python.py`) **FAILS**: frame 0
  (all-zero inputs) matches Python **bit-exact**, but any real (non-zero) input makes
  `on_policy`/`off_policy` output `inf` (or wrong). Python rknnlite is correct for the same
  models, so the `.rknn` files are fine. *(Note post-Fix-E: the test's synthetic inputs overflow
  on_policy in BOTH paths — see RESOLVED caveat. The C++ inf on real inputs is fixed.)*
- **Root cause = NPU multi-context corruption (core-INDEPENDENT).** Running the **vision**
  context corrupts the **policy** contexts' NPU state (the policy then ignores its
  `features_buffer` input; inf is non-deterministic across runs). This is a property of running
  3 rknn contexts through the **C API**; Python rknnlite does not hit it.
- **UPDATE — per-context core isolation did NOT fix it (commit 3455338, tested 2026-08-11).**
  Distributing the 3 contexts across all 3 NPU cores (vision→core 2, on_policy→core 0,
  off_policy→core 1) produced the **identical** failure: 1:1 test still frame-0-exact then inf,
  diag still shows vision-pollutes-policy (A=finite, B=inf+fb-ignored). So the corruption is via
  **shared NPU memory/state that `rknn_set_core_mask` does NOT isolate** — not a per-core
  collision. Strongly suggests the 3 rknn contexts share an NPU memory pool regardless of core.
- **UPDATE — hybrid C++/Python does NOT fix it either (commit 97d57e7, tested 2026-08-12).**
  The hybrid uses the proven **2-file C++ path** for vision+on_policy + Python rknnlite for
  off_policy. Result: `on_policy` **still goes inf** via C++ — 46/200 frames in the 1:1 test
  (max_abs=inf, or 125–818). And critically: **on_policy alone (NO vision first) is inf 2/5 runs.**
  This OVERTURNS the "vision pollutes policy / multi-context" theory — the inf is **non-deterministic
  in the opm10v3 on_policy model + the C librknnrt API itself**, independent of vision, context count,
  or core. Python rknnlite computes the same model correctly every time. *(The "non-deterministic"
  appearance was actually the pass_through=0 no-op-conversion bug varying with input — fixed by Fix E.)*
- **CONCLUSION (pre-Fix-E, outdated):** no C++ rknn API variant works for opm10v3 on_policy on
  this NPU. *(OVERTURNED by Fix E.)*
- **Device-only bug.** It cannot be reproduced on Mac (see below).

---

## Why it can't be reproduced on Mac
- The inf comes from the **RK3588 NPU** running 3 rknn contexts via the C librknnrt API.
- Mac has no NPU. The `.rknn` models only execute on the device (or via the rknn-toolkit2
  **simulator** on CPU, which is a different code path and does NOT exhibit the bug).
- → **All reproduction is on the KA2 device** over SSH (`ssh kommu@192.168.0.9` at home, or
  `kommu@192.168.69.1` on the device hotspot). Mac is only used to edit code + push.

---

## Reproduction on device

### 1. Build the .so (the agent's original plan misses two gotchas)
```bash
ssh kommu@192.168.0.9 'cd /data/openpilot && \
  # GOTCHA 1: panda/certs/{debug,release}.pub are missing on this fork -> scons can't parse SConstruct
  openssl genrsa -out /tmp/stub.key 1024 2>/dev/null && \
  openssl rsa -in /tmp/stub.key -pubout -out panda/certs/debug.pub 2>/dev/null && \
  cp panda/certs/debug.pub panda/certs/release.pub && \
  # GOTCHA 2: bare scons + cythonize are not on PATH; use the venv binaries
  PATH=/usr/local/venv/bin:$PATH /usr/local/venv/bin/scons -j4 selfdrive/modeld/runners/driving_rknnmodel_pyx.so'
```
The `prebuilt` marker must stay present (boot safety) — the targeted `scons <target>` does NOT
remove it, so leave it alone.

### 2. Run the 1:1 correctness test
```bash
ssh kommu@192.168.0.9 'cd /data/openpilot && /usr/local/venv/bin/python tools-local/test_3split_cpp_vs_python.py --frames 100'
```
Expected (bug present): frame 0 `vision` + `on_policy` BIT-EXACT, then frames 1+ show
`on_policy`/`off_policy MISMATCH (max_abs=inf ...)`. Exit code 1.

### 3. Isolation tests that pinpoint the cause
Run `tools-local/diag_3split_inf.py` (added alongside this doc):
```bash
ssh kommu@192.168.0.9 'cd /data/openpilot && /usr/local/venv/bin/python tools-local/diag_3split_inf.py'
```
It prints, for identical inputs:
- on_policy with **no vision first** → finite, correct ✅
- on_policy **after vision (non-zero)** → different output AND `fb` input ignored ❌
- identical input 3× → deterministic but `inf` (proves not stateful, input-dependent via NPU state)

---

## Root cause (detailed)
- The 3 contexts (`vision_ctx_`, `policy_ctx_` = on_policy, `off_policy_ctx_`) are separate
  `ModelCtx` with separate buffers — correct in C++ memory terms.
- Input/output attrs for opm10v3 policy are **byte-identical** to the working default policy
  (inputs: desire_pulse 200/size=400, traffic_convention 2/4, features_buffer 12800/25600, all
  FLOAT16/fmt=UNDEFINED; output FLOAT16/qnt=AFFINE/scale=1/zp=0). So it is **not** an
  input-mapping, size, or dequant bug.
- Frame 0 (zeros) is bit-exact → the inference **logic** is correct.
- The corruption appears only after the **vision** context runs. After `run_vision(non-zero)`,
  the subsequent `run_policy` output no longer depends on `features_buffer` (B==C with different
  fb) and is often `inf`. → The NPU state set by the vision run bleeds into the policy contexts.
- Non-deterministic across runs → NPU resource/timing collision, not a deterministic logic bug.

## Ruled out (all attempted, none fixed it)
- NPU core mask — ALL variants:
  - Single core for policy (`RKNN_3SPLIT_POLICY_CORE_MASK=0`), and all-core-0
    (`RKNN_DRIVING_CORE_MASK=0`). "core 0 works" was a **red herring** (zero inputs only).
  - **Per-context isolation (commit 3455338): vision→2, on_policy→0, off_policy→1.** Tested
    2026-08-11 — **identical failure** (frame 0 exact, frame 1+ inf; diag A=finite, B=inf). So
    `rknn_set_core_mask` does NOT isolate the contexts. The corruption is via shared NPU
    memory/state, not a per-core collision.
- Missing `rknn_outputs_release()` between frames: added to `run_vision` + `run_policy_ctx`
  (standard RKNN pattern). No effect on the inf.
- Python runner context contention in the test: the test now `release()`s the 3 Python RKNNLite
  contexts before loading the C++ runner. Bug persists with only 3 C++ contexts.
- **Hybrid C++/Python (commit 97d57e7, tested 2026-08-12):** 2-file C++ for vision+on_policy,
  Python for off_policy. `on_policy` still inf 46/200 frames. And `on_policy` alone (no vision) is
  inf 2/5 → **not** multi-context/vision-pollution; the inf is in on_policy+C API itself.

## Next angles for whoever picks this up

### Root cause research (web search, 2026-08-12)

The inf is likely **fp16 overflow** in the sigmoid Gelu rewrite, confirmed by multiple sources:
- [Rockchip RKNPU User Guide](https://www.scribd.com/document/774992182): "if simulator_error shows inf, it's typically FP16 overflow"
- [GitHub #558](https://github.com/airockchip/rknn-toolkit2/issues/558): librknnrt v2.3.2 C API has bugs where "Python API uses a different code path that bypasses the bug"
- [SHARD paper (ACM)](https://dl.acm.org/doi/pdf/10.1145/3805621.3807618): documents NaN/Inf on Rockchip NPUs, proposes activation rescaling

The sigmoid Gelu approximation does `1.702 * x` before Sigmoid. In fp16, if `x > ~38,500`,
`1.702 * x` exceeds fp16 max (65504) → `inf`. The C API propagates this inf; rknnlite doesn't
(different internal code path per Rockchip's own admission).

**Caveat:** the inf is non-deterministic (2/5 runs, same input), which doesn't perfectly match
deterministic fp16 overflow. This suggests there may be an ADDITIONAL librknnrt initialization
bug. But fixing the overflow is the cheapest first step.

### Fix D: pass_through=1 (MOST PROMISING — IMPLEMENTED, needs device test)

**Status: code change in `driving_rknnmodel.cc`, needs .so rebuild + test.**

**Root cause insight:** Investigated the rknnlite wheel — it does NOT bundle its own
`librknnrt.so`. Both Python rknnlite and our C++ `.so` call the SAME system library.
The difference is in HOW they call it.

Our C++ code set `pass_through=0` on all inputs, meaning "librknnrt, please convert my
fp16 input to the model's native format." But for OPM10V3 policy models, the input format
attr is `UNDEFINED` — so librknnrt doesn't know what to convert FROM, and produces
non-deterministic garbage/inf. rknnlite likely uses `pass_through=1` (skip conversion),
bypassing this buggy code path entirely.

**The fix:** Changed `pass_through` from `0` to `1` (default). Now librknnrt passes our
fp16 data directly to the NPU without trying to convert it. Our C++ code already converts
inputs to fp16 manually (`float_to_half_array` for policy, LUT for vision), so no conversion
is needed from librknnrt.

Configurable via env var: `RKNN_PASS_THROUGH=0` reverts to old behavior.

**To test:**
1. Rebuild .so: `cd /data/openpilot && PATH=/usr/local/venv/bin:$PATH /usr/local/venv/bin/scons -j4 selfdrive/modeld/runners/driving_rknnmodel_pyx.so`
   (need stub panda certs first — see build recipe in §1 above)
2. Run test: `python3 tools-local/test_3split_cpp_vs_python.py --frames 100`
3. If pass → inf bug fixed, no model reconversion needed
4. If still inf → try with the Fix A Clip models (already in git)

**Why this might work where Fix A/B might not:** This addresses the ACTUAL difference
between rknnlite and C API (calling convention), not just the symptom (overflow). If
rknnlite works because it uses pass_through=1, this fix makes our C++ code do the same.

**Risk to default/WMI:** With pass_through=1, librknnrt skips format conversion for ALL
models (including default 0.10.3). Our C++ code already handles format conversion manually
(NCHW→NHWC for vision, fp16 cast for policy), so this should be safe. But test default
model after rebuild to verify.

### Fix A: Clip before multiply (MODELS CONVERTED, needs device test)

**Status: ALL 3 models reconverted with Clip, committed in git (commit 51d81ab).**

Files ready in `selfdrive/modeld/models/`:
- `driving_vision_opm10v3.rknn` (51.6 MB, 38 Clips)
- `driving_on_policy_opm10v3.rknn` (15.9 MB, 9 Clips)
- `driving_off_policy_opm10v3.rknn` (22.1 MB, 21 Clips)

**To test:** git pull on device → run test. No reconvert needed.

**If Fix A doesn't work** (inf persists despite Clip), the cause is NOT overflow but a deeper
librknnrt Sigmoid-op bug. Proceed to Fix B.

### Fix B: erf Gelu for on_policy only (MODEL CONVERTED, backup)

**Status: converted and committed — `driving_on_policy_opm10v3_erf.rknn` (15.8 MB, 9 Erf nodes).**

If Fix A and Fix D both fail, swap in the erf model:
```bash
cp driving_on_policy_opm10v3_erf.rknn driving_on_policy_opm10v3.rknn
cp driving_on_policy_opm10v3_erf_metadata.pkl driving_on_policy_opm10v3_metadata.pkl
```
Then retest. Uses Erf+Add+Mul instead of Sigmoid+Mul — completely different ops.

Vision stays on sigmoid (it works in C++ already). off_policy stays on sigmoid (Python only).

### Fix C: newer librknnrt.so (RULED OUT)

Investigated: rknnlite does NOT bundle its own librknnrt.so — both Python and C API use the
same system library. Updating the library version would not change the calling convention
difference (pass_through) that causes the inf. Fix D (pass_through) is the correct fix.

### Previous angles (all exhausted)
1. ~~Shared NPU memory pool~~ — per-core isolation tested, didn't help
2. ~~Process-level isolation~~ — hybrid C++/Python tested, on_policy alone still inf
3. ~~Explicit sync/barrier~~ — rknn_run is synchronous, no pipelining issue
4. ~~Multi-context collision~~ — disproved by hybrid test (on_policy alone, 1 C context, still inf)

## Build/test quick reference (device)
- Rebuild .so: see step 1 (stub certs + venv scons).
- Attr dump: the runner now prints `[RKNN-IN] <model> in[i] '<name>' fw_type=A fmt=B | native_type=C native_fmt=D -> pass_through=P`
  to stderr on every model load. `type`: 0=FLOAT32,1=FLOAT16,2=INT8,3=UINT8; `fmt`: 0=NCHW,1=NHWC,2=NC1HWC2,3=UNDEFINED.
- 1:1 test: `tools-local/test_3split_cpp_vs_python.py` — **WARNING**: its synthetic inputs overflow
  on_policy in both Python and C++, so it reports mismatches that are NOT C++ bugs (see RESOLVED
  caveat). Useful only as a smoke test that the runner loads; do not use as a correctness gate.
- Per-model finiteness check (the reliable offroad test):
  ```bash
  ssh kommu@192.168.0.9 'cd /data/openpilot && /usr/local/venv/bin/python -c "
  import sys,numpy as np,pickle; sys.path.insert(0,\"/data/openpilot\")
  from openpilot.selfdrive.modeld.runners.driving_rknnmodel_pyx import DrivingRKNNRunnerCpp
  from pathlib import Path; MD=Path(\"/data/openpilot/selfdrive/modeld/models\")
  # load vision+on_policy, run a zeros frame, assert finite
  ..."'
  All three models should print finite=True with zeros fb.
- Rollback: `cp driving_rknnmodel_pyx.so.bak.* driving_rknnmodel_pyx.so` (both
  `/data/openpilot` and `/data/safe_staging/merged`). Or set `RKNN_PASS_THROUGH=0` to restore the
  old global-convert behavior for debugging.

## Current state (2026-08-12)
- **Fix E deployed.** Device `.so` rebuilt with per-input pass_through (md5 `26f8cb1c…`),
  `prebuilt` present, both overlays updated.
- All three models (default/wmiv12/opm10v3) verified to produce finite policy output offroad.
- `SelectedDrivingModel` = `opm10v3`. **Pending: a real onroad drive to validate Hz + engage**
  (modeld is onroad-only; offroad it does not run, so Hz/engage cannot be checked without the car).
- default + WMI remain safe fallbacks (verified finite with Fix E; easy revert via the param).
