# OPM10V3 C++ 3-Split Runner — Bug Findings & Reproduction (HANDOFF)

> Status: the C++ 3-split runner (`driving_rknnmodel_pyx`, agent commit `cab02c8`) **builds and
> keeps 2-file backward-compat working, but the 3-file policy path is broken**. Root cause
> identified; not yet fixed. This doc is the full record so anyone can reproduce + continue.

## TL;DR
- The 1:1 correctness test (`tools-local/test_3split_cpp_vs_python.py`) **FAILS**: frame 0
  (all-zero inputs) matches Python **bit-exact**, but any real (non-zero) input makes
  `on_policy`/`off_policy` output `inf` (or wrong). Python rknnlite is correct for the same
  models, so the `.rknn` files are fine.
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
  or core. Python rknnlite computes the same model correctly every time.
- **CONCLUSION: no C++ rknn API variant works for opm10v3 on_policy on this NPU** (3-file, 2-file
  hybrid, per-core, all intermittent inf). The opm10v3 on_policy `.rknn` is effectively incompatible
  with the C API here. Viable paths: (a) re-quantize/re-export on_policy so the C API handles it,
  (b) run opm10v3 fully on Python rknnlite (correct but ~17 Hz → blue/no-engage on KA2), or
  (c) abandon opm10v3 on KA2 and use default/WMI (which work via the 2-file C path).
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
- 1:1 test: `tools-local/test_3split_cpp_vs_python.py` (exit 0 = pass).
- Attr dump (if needed): the runner prints attrs if you temporarily re-add the `DBG3SPLIT`
  printf block; `rknn_tensor_type`: 0=FLOAT32,1=FLOAT16,2=INT8; `rknn_tensor_format`: 0=NCHW,1=NHWC,3=UNDEFINED.
- Rollback: `cp driving_rknnmodel_pyx.so.bak.* driving_rknnmodel_pyx.so` (both
  `/data/openpilot` and `/data/safe_staging/merged`). With the old .so, `ModelState3SplitRKNN`'s
  3-file ctor raises TypeError → automatic Python-rknnlite fallback (safe).

## Current state (2026-08-11)
- Device `.so` = clean backup (md5 `9cdf2bf8…`), `prebuilt` present, `ModelState3SplitRKNN`
  falls back to Python rknnlite. default + WMI (2-file C++) work. Safe to drive.
- The agent's C++ source (`cab02c8`) is intact in git + on device; only the built `.so` is the
  old one. `SelectedDrivingModel` is currently `wmiv12`.
- opm10v3 cannot engage on KA2 until this is fixed (Python fallback = ~17 Hz → blue).
