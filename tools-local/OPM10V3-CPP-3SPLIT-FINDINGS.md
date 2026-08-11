# OPM10V3 C++ 3-Split Runner — Bug Findings & Reproduction (HANDOFF)

> Status: the C++ 3-split runner (`driving_rknnmodel_pyx`, agent commit `cab02c8`) **builds and
> keeps 2-file backward-compat working, but the 3-file policy path is broken**. Root cause
> identified; not yet fixed. This doc is the full record so anyone can reproduce + continue.

## TL;DR
- The 1:1 correctness test (`tools-local/test_3split_cpp_vs_python.py`) **FAILS**: frame 0
  (all-zero inputs) matches Python **bit-exact**, but any real (non-zero) input makes
  `on_policy`/`off_policy` output `inf` (or wrong). Python rknnlite is correct for the same
  models, so the `.rknn` files are fine.
- **Root cause = NPU multi-context corruption.** Running the **vision** context corrupts the
  **policy** contexts' NPU state (the policy then ignores its `features_buffer` input; inf is
  non-deterministic across runs). This is a property of running 3 rknn contexts through the **C
  API**; Python rknnlite does not hit it.
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
- NPU core mask: pinned policy heads to core 0 (`RKNN_3SPLIT_POLICY_CORE_MASK`), also tried
  all-core-0 (`RKNN_DRIVING_CORE_MASK=0`). The earlier "core 0 works" was a **red herring** — it
  only worked because the test used zero inputs.
- Missing `rknn_outputs_release()` between frames: added to `run_vision` + `run_policy_ctx`
  (standard RKNN pattern). No effect on the inf.
- Python runner context contention in the test: the test now `release()`s the 3 Python RKNNLite
  contexts before loading the C++ runner. Bug persists with only 3 C++ contexts.

## Next angles for whoever picks this up
1. **Per-context NPU memory isolation.** Investigate whether `rknn_init` for the 3 contexts
   shares/overlaps NPU memory. Try `rknn_set_core_mask` per-context at **init** with distinct
   cores (vision=2, on_policy=0, off_policy=1) — note dmonitoringmodeld uses cores 0+1, so check
   contention.
2. **Explicit sync/barrier** between `run_vision` and `run_policy` (the C API `rknn_run` is
   synchronous, but the NPU may pipeline across contexts).
3. **Is the 3-split even viable via the C API on this NPU?** Python rknnlite (used by the
   fallback) works, so the models run fine on the NPU — the issue is specific to the C runner
   holding 3 contexts. Consider keeping opm10v3 on the **Python rknnlite** runner (correct but
   ~17 Hz → blue/no-engage on KA2) unless the C collision is solved, OR re-quantize to a 2-file
   layout so it can use the proven 2-file C path.

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
