# Bukapilot (Kommu) — Proton Investigation Findings

> Reference doc compiled from a deep code review of the **bukapilot** repo (Kommu's openpilot fork).
> Date: 2026-07-30. All claims cite `file:line` and were verified against the code.

---

## 0. Context

- **Repo:** `github.com/kommuai/bukapilot`
- **Branches (verified `git ls-remote`):**
  - `beta` = **KA2 / kommu 2, current shipping release** (checked out locally). Base = pre-0.10 openpilot. Tip commit `177176a` (2026-04-18). **All paths/analysis below are for `beta` unless noted.**
  - `snapshot` = **KA1 / kommu 1**. Base = classic openpilot. Tip commit `3aa8238` (2026-07-17).
  - `staging` = **bukapilot v10.1.0 = openpilot 0.10.3** (in active development, 2026-07-30). The 0.10 move Kommu is working on — **the branch to base X70-on-0.10 work on** (see §13).
- **Proton cars:** S70, X50, X70, X90. Code in `selfdrive/car/proton/` on `beta`; **moves to `opendbc_repo/opendbc/car/proton/` on `staging` (0.10 moved car code into the opendbc package).**
- **All Proton cars use the PID lateral controller** (`lateralTuning.init('pid')`), NOT the torque controller.
- Proton code is **fully owned by the Kommu team**.

---

## 1. Lateral (Steering)

### 1a. KA2 collapsed all 4 cars onto one shared PID baseline

KA1 had **per-car** tuning. KA2 replaced it with a single shared baseline (set at top of `_get_params`) that matches **none** of KA1's values. Only X90 overrides anything (`kiV`).

**The dominant change = feed-forward gain `kf` (the main "strength" lever):**

| Car | KA1 `kf` | KA2 `kf` | Change |
|---|---|---|---|
| X50 / S70 / X90 | 0.0000015 | **0.00007** | **~46×** |
| X70 | 0.000006 | **0.00007** | **~12×** |

Plus top integral gain `kiV` roughly **2.5×** (KA1 ~0.16 → KA2 0.40). Feed-forward + integral up = steering commands far more torque for the same desired angle → **directly explains "feels very strong / harder to override" on KA2.**

### 1b. Per-car comparison (KA1 → KA2)

**PID gains** (`selfdrive/car/proton/interface.py`):

| Param | KA1 X50/S70/X90 | KA1 X70 | KA2 ALL (shared) | X90 override (KA2) |
|---|---|---|---|---|
| `kpBP` | `[0,25,35,40]` | `[0,5,15,25,35]` | `[0,5,25,35,40]` | — |
| `kpV` (top) | `[...0.19]` | `[...0.17]` | `[0.05,0.05,0.15,0.15,0.16]` | — |
| `kiV` (top) | `[0.04,0.08,0.16]` | `[0.001,0.01,0.09,0.4,0.5]` | `[0.05,0.10,0.20,0.40]` | `[0.05,0.05,0.05,0.05]` |
| `kf` | 0.0000015 | 0.000006 | **0.00007** | — |

**STEER_MAX (`lateralParams.torqueV[0]`):**

| Car | KA1 | KA2 | Δ |
|---|---|---|---|
| X50 | 545 | 545 | same |
| S70 | 545 | 530 | −15 |
| X70 | 500 | 500 | same |
| X90 | 545 | **256** | **halved** (KA2 doubles the CAN cmd for X90: `carcontroller.py:125-128`) |

**Rate limits (`STEER_DELTA_UP/DOWN`, `carcontroller.py:36-41`):**
- KA1 all: 20 / 30
- KA2 S70/X50/X70: 15 / 35
- KA2 X90: 4 / 8

**Vehicle dynamics:**

| Param | KA1 | KA2 | Notes |
|---|---|---|---|
| `steerActuatorDelay` | X70=0.13, rest=0.30 | **0.30 all** | X70 lost 0.13 |
| `steerRatio` | X70=16.0, rest=15.0 | **15.0 all** | X70 lost 16.0 |
| `tireStiffnessFactor` | 0.9871 | **0.7933** | (0.7933 = Toyota Camry's value — looks copy-pasted) |
| `wheelSpeedFactor` | 1 | 1.02 | changed all |
| mass (base) | X50 1370, S70 1312, X70 1610, X90 1740 | X50 1370, S70 1300, X70 1610, X90 1705 | minor |

### 1c. S70 & X50 specifically (Kommu dev owns both)

- **KA1:** S70 and X50 had **identical** specific lateral tuning (`kf=0.0000015`, `kiV=[0.04,0.08,0.16]`, STEER_MAX 545).
- **KA2:** Both on the **shared baseline, no lateral-PID overrides.** Only differ in STEER_MAX (X50=545, S70=530); X50 also has a longitudinal override. → Both absorbed the 46× `kf` jump.

### 1d. "Changing tuning values didn't change the feeling" (dev's observation)

The code **clearly applies** the tuning (`actuators.steer` → `× STEER_MAX` → CAN, `carcontroller.py:76`). So changing `kf/kp/ki` *should* change output. If the dev felt nothing, likely causes:
- They changed `kp/ki` (responsiveness) but **not `kf`** — and `kf` dominates strength, so "strong" feel wouldn't budge; or
- A **param-reload/caching** issue on the device.

**Conclusion: `kf` (and `ki`) is the real strength lever.** Those are exactly what KA2 inflated 12–46×.

### 1e. Torque controller (NOT used by Proton — why / how / what's available)

**What's available in kommu 2** (the full torque-controller stack is present, inherited from openpilot):
- `latcontrol_torque.py` — the controller (works in lateral-accel space, with a small PID on top).
- `torqued.py` — the self-learner: fits `latAccelFactor`/`friction` from real driving, publishes `liveTorqueParameters`. **Allow-listed** to `['toyota','hyundai']` only (`torqued.py:35,73`) → Proton excluded.
- `configure_torque_tune()` (`interfaces.py:206`) + `get_torque_params()` — loads per-car params from the toml files.
- `torque_data/` — three seed files: `params.toml` (measured values per car), `override.toml` (manual overrides), `substitute.toml` (remap one car → another's values). Loader order: substitute → override → params. **No `PROTON X70`/S70/X50/X90 entry in ANY of them.**
- `neural_ff_weights.json` — fork-only neural feedforward, used by **GM Bolt EUV only** (inherited, not Kommu's).
- Selection: `controlsd.py:246` routes to the torque controller when `lateralTuning.which() == 'torque'`.

**Why Proton doesn't use it:**
1. **Not set up** — no seed entry, so calling `configure_torque_tune` would throw `NotImplementedError`. Proton's `interface.py:26` uses `lateralTuning.init('pid')` instead.
2. **No auto-learning for Proton** — `torqued` is allow-listed to Toyota/Hyundai, so even on the torque controller the X70 would run on a **frozen static seed**, never refined by driving. The torque controller's whole selling point (self-adapting) is **OFF** for Proton.
3. **Kommu dev's empirical verdict:** tried torque on the X70, **PID feels better**. Reasoning: the torque controller mainly helps low/poor-torque cars; "the X70 already has good torque," so no gain. → **Stick with PID.**

**How you'd switch the X70 to it (if you wanted to):**

*Step 1 — the seed (verified mechanics, `interfaces.py:48-68`):* `configure_torque_tune(candidate)` looks up the **fingerprint string** (`"PROTON X70"`, from `values.py:37`) in the toml files, in order **substitute → override → params**. It raises `NotImplementedError` if found in none, `RuntimeError` if in more than one — so add it to **exactly one** file. Format: `"KEY" = [LAT_ACCEL_FACTOR, MAX_LAT_ACCEL_MEASURED, FRICTION]`.
- **Easiest — `substitute.toml` (one line, no guessing):** `"PROTON X70" = "HYUNDAI SANTA FE 2019"` → borrows `[3.079, 2.617, 0.121]`.
- **Or `override.toml` (your own numbers):** `"PROTON X70" = [2.5, 2.2, 0.12]`. (Right file for community/manual values; `params.toml` is for measured fleet data.)
- Value ranges (typical SUV, from real params.toml entries): `LAT_ACCEL_FACTOR` ~2.0–3.1 (Santa Fe 3.08, Tucson 2.96, RAV4 2.09); `MAX_LAT_ACCEL_MEASURED` ~2.0–2.9 m/s² (**safety cap**, conservative ~2.0–2.5; ISO hard ceiling is 3.0); `FRICTION` ~0.10–0.15.
- Real values come from `torqued` fitting your driving — but it's allow-listed, so it won't run for Proton unless you ALSO add `'proton'` to `ALLOWED_CARS` (`torqued.py:35`); then it learns `latAccelFactor`/`friction` within ±30% of your seed. Without that edit, the seed is **frozen/static**.

*Step 2 — interface.py:* for `CAR.X70` replace the PID block with `CarInterfaceBase.configure_torque_tune(candidate, ret.lateralTuning)` (this sets `lateralTuning.init('torque')`; `controlsd.py:246` then routes to the torque controller; `steerControlType = torque` is already set at `interface.py:22`).

Minimum viable switch = **one `substitute.toml` line + the interface.py change**. Auto-learning needs the `ALLOWED_CARS` edit too.

**What you'd gain/lose:** without the `ALLOWED_CARS` edit you'd get the torque controller's **physics-model feedforward + friction/roll compensation** (sometimes smoother than PID) but **NOT** self-adaptation — so it behaves like a static tune, same as PID, and the dev found PID better anyway. Net: not worth it for the X70. (See §8c and §9.)

**Where learned values live + update behavior (verified `torqued.py:94-119,242`):** if `torqued` is enabled and learns, the values are stored in the **openpilot Params store** under key `LiveTorqueParameters` (`params.put_nonblocking`), which lives on the **device filesystem — NOT in the git repo.** So:
- A normal GitHub update / `git pull` does **not** wipe learned values (Params isn't part of the repo).
- They **are discarded + re-learned from the seed** if the update changes the **seed** (`friction`/`latAccelFactor`), the **`VERSION`** constant, the **carFingerprint**, or the **controller type** — via a restore-key check `get_restore_key = (carFingerprint, lateralTuning.which(), friction, latAccelFactor, version)`. This is deliberate: a stale value fit to an *old* seed is thrown out rather than misapplied.
- Learned values are always **clamped to ±30% of the seed** anyway.
- Only a **factory reset / reflash** fully clears Params.
- Reminder: this only applies **if `torqued` runs for Proton** (needs `'proton'` in `ALLOWED_CARS`) — by default it doesn't, so nothing is learned/stored today (X70 is on PID).

### 1f. Drive-data lateral analyzer (added 2026-08-16)

`result/lateral_flm.py` — FLM-style lateral analyzer ported from StarPilot (MIT).
Standalone (no openpilot imports); reads `result/drives/*--<seg>.rlog.zst` and writes
per-route JSON reports to `result/flm_reports/`. Tests in `result/test_lateral_flm.py`.

- **Signals**: desired la = `controlsState.desiredCurvature * vEgo²`; actual la =
  `cameraOdometry.rot[2] * vEgo` (carState.yawRate is unpopulated on Proton); PID
  internals from `lateralControlState.pidState`; `pidState.active` gates eligibility.
- **Output**: event counts/severity by speed band (understeer, oversteer, late/early
  turn-in, unwind too slow/fast, low-speed unwillingness, saturation, center chatter,
  curve oscillation) + PID diagnostics (p/i/f output split, utilization, angle-error RMS).
- **Baseline (6 drives, 2026-08-09/10, all on pre-retune PID tuning)**: i-fraction
  0.5–0.7 of output at mid/fast speeds, kf negligible (~2% low-speed), `late_turn_in`
  dominant (90–180 events/route) with understeer ≫ oversteer — independent confirmation
  of the 2026-08-13 retune rationale (1.5× kp, 6× kf, lowered ki @54 km/h).
- **Caveat**: thresholds are firestar's, tuned on torque cars; treat aggregates, not
  single low-count events. Sign convention: curvature-family signals are sign-opposite
  to steering angle (cross-validated |corr| 0.995/0.978).
- **Workflow**: retune → drive → re-run → diff `result/flm_reports/*.json` vs previous.

---

## 2. Longitudinal (ACC)

### 2a. Both branches have `openpilotLongitudinalControl = True`
- KA1 `snapshot/interface.py:34`, KA2 `interface.py:44`.
- KA1 "didn't support it" in practice was a **hardware/compute** limitation (Kommu dev), not code — vision-based lead tracking needs compute.

### 2b. Two modes via the `stock-acc` device feature (`FeaturesPackage`)
- **stock-acc OFF (default):** openpilot does longitudinal. safetyParam=1.
- **stock-acc ON:** Proton's own stock ACC does speed/following; openpilot only steers. safetyParam=2.
- It's a **user-toggleable setting** (one of: `clear-code`, `ignore-dm`, `lks-tactile`, `stock-acc`).

### 2c. The blend — openpilot "spoofs" the stock ACC_CMD and blends
`carcontroller.py:136-148`:
```python
if self.openpilot_long:
  accel_cmd = accel_cmd * 15 if accel_cmd >= 0 else accel_cmd * 18   # m/s^2 -> Proton CAN units
  if CS.out.gasPressed: accel_cmd = 0
  mult = interp(vEgo, [0, 28.3], [1.0, 0.6])
  if vEgo < 2.5:
    accel_cmd = (CS.stock_acc_cmd * mult + accel_cmd) / 2            # AVERAGE at low speed
  else:
    accel_cmd = min(CS.stock_acc_cmd * mult, accel_cmd)             # MIN at higher speed
  can_sends.append(create_acc_cmd(...))
```

**Key properties of the blend:**
- `min` is **asymmetric**: caps **gas (acceleration)** at stock, but allows openpilot to **brake harder** than stock (more-negative wins). So openpilot can trim/follow-closer via braking, but can't accelerate harder than stock.
- **Safety floor:** because gas is capped at the manufacturer-certified stock ACC, openpilot can never make the car accelerate beyond what the proven system would. Errors get damped against stock. (Tradeoff: can't aggressively close gaps.)
- **No pure-openpilot toggle exists.** Two modes only (stock-acc on/off). Pure openpilot would require code edit (remove the blend).

### 2d. Why blend (and why ×15 is hardcoded)
- The Proton stock ACC ECU is **always live** and computing a command (read from cam bus `ACC_CMD`, `carstate.py:96`). openpilot rides on top of it rather than replacing it — keeps the ECU happy, hedges scaling error, safety cap via `min`.
- **×15/×18 is a consequence of the blend**, not a disregard of openpilot's clean approach. Upstream Toyota does `"ACCEL_CMD": accel` (clean pass-through, DBC scales units). Kommu can't, because (a) Proton's ACC_CMD isn't a clean linear accel field (×15 gas vs ×18 brake = piecewise, can't be a single DBC scale), and (b) the blend **requires** openpilot's accel to be in Proton's native units to average/min against `stock_acc_cmd`. **The blend and the ×15 are the same design decision.**

### 2e. Following distance (gap)
- Gap button → `SET_DISTANCE` (`carstate.py:154`) → `set_long_personality(distance_val - 1)` → openpilot `LongitudinalPersonality` (aggressive/standard/relaxed, `interfaces.py:429`) → fed to MPC planner as a weight (`longitudinal_planner.py:133,137`).
- **Proton is radarless** (`radar_interface.py` is an empty stub) → lead distance measured by the **camera/AI model**, not radar. Following-distance accuracy depends on the vision model.
- Because the blend caps gas but not brake, kommu **can follow closer than stock** (brake later + planner targets smaller gap), but can't aggressively *close* gaps. At crawl (<2.5 m/s) it averages with stock → advantage muted.
- Stock-side distance byte `SET_ME_X6A` (`protoncan.py:40-41`) — ECU magic for SNG resume, not openpilot's gap logic.

### 2f. ACC does NOT auto-learn
- openpilot's learners (`torqued.py`, `paramsd.py`) are **lateral only**. No longitudinal live-learning process exists (searched — none).
- ACC is entirely **configured**: fixed offline-trained model (lead detection) + hardcoded MPC weights (per personality) + hardcoded LongControl PID gains + kommu's hardcoded blend numbers.
- Upstream ALSO hardcodes long PID gains per car (Toyota/Honda/Hyundai/VW all do) — so Kommu isn't unusual there. LongControl gains aren't learned anywhere.
- The accel decision comes from the **MPC planner** (using the model's lead detection), NOT directly from the AI model. Model = eyes, planner = brain.

---

## 3. Safety Model — why Proton differs from normal openpilot

**What the safety model is & why it's needed:** openpilot has two brains — the Python code (on the main computer) and the **panda**, a *separate microcontroller* that's the physical gateway to the car's CAN bus. The safety model is firmware **on the panda** — an independent, hardware-level last line of defense. Even if the Python crashes or bugs out, the panda can still clamp/block dangerous commands. Its hooks:
- `tx_hook` — filter/clamp what's sent (steer max, rate limits, zero all actuation when `controls_allowed=false`).
- `rx_hook` — read brake/gas/driver-torque/speed/cruise and set `controls_allowed`.
- `rx_checks` — validate incoming CAN (counter/checksum/frequency).
- `fwd_hook` — route/block messages between the car bus and ADAS bus.

It's defense in depth: the software can fail, but the firmware gatekeeper must still keep actuation bounded and stop it the instant it should (brake / disengage / driver override). **The safety model is purely a safety interlock — it does NOT explain the "changing PID does nothing" or "single shared values" mysteries** (see §1d and the hypothesis below).

> **Hypothesis for the two open mysteries (not code-confirmable, needs device debug):** the dev was likely tweaking the **per-car overrides** (small car-specific diffs, e.g. STEER_MAX 500 vs 530), while the dominant steering "strength" comes from the **shared baseline** (`kf` 46×, `ki` 2.5×) which is identical across all cars. So changing per-car values barely moved the feel → "tuning does nothing" → they unified to shared values. The real KA1→KA2 delta (the shared `kf`) was never the lever they were turning. Could also be a param-caching/reload issue on the device.

- **KA1 (snapshot):** `panda/board/safety/safety_proton.h` **does not exist** (zero `proton` refs in `panda/`). The `SafetyModel.proton` enum existed but no firmware impl.
- **KA2 (beta):** `safety_proton.h` is **new but a permissive no-op** (`:8-23`):
  - `proton_rx_hook`: hardcodes `vehicle_moving = true; controls_allowed = true;` ignores packet.
  - `proton_tx_hook`: `return true;` — lets every steer command through unfiltered.
  - `proton_rx_checks[]`: empty.
- **Normal openpilot cars (Toyota/Honda)** enforce in firmware as last line of defense: `MAX_STEER`, ramp-rate limits, real-time torque limits, driver-torque measurement + wind-down, `controls_allowed` gating (steer zeroed on brake/cruise-off/override). **Proton has none of this.**
- **Driver-override is inert in BOTH branches:** `apply_proton_steer_torque_limits(new_steer, self.last_steer, 0, ...)` — `driver_torque` hardcoded to `0`, byte-for-byte identical KA1↔KA2. So the proportional driver-handoff has always done nothing.
- **`steeringPressed` threshold:** KA1 = `124` (`carstate.py:115`) → KA2 = `65` (`carstate.py:142`). Halved, but only feeds the "steering required" alert — does NOT reduce steer torque.

**Implication:** The "stronger/harder to override" KA2 feel is **not** from a safety-model weakening or override change — those were absent/identical in both. It's from the **PID gain increases** (chiefly `kf` 46×, `ki` 2.5×).

**Effect on a torque-controller experiment:** none that differs from PID. Controller type (PID vs torque) and the safety model sit at different layers — both produce `actuators.steer` that flows through the same Proton carcontroller (`STEER_MAX`, rate limits) and the firmware passes it through unchecked either way. The permissive safety is **controller-agnostic**; it won't cap or distort a torque test relative to PID. What matters for a torque test is the seed + `torqued` learning, not the safety model. (Caveat: no firmware backstop for *either* controller — a misbehaving torque controller has the same lack of net as PID today.)

---

## 4. Code Quality vs Upstream commaai/openpilot

(Below upstream's bar for a *supported* car. X50/S70/X90 functional but rough; X70 is a placeholder.)

**HIGH severity:**
1. **X70 shipped "supported" with a placeholder fingerprint** — `# TODO: get the real fingerprint`, a verbatim copy of S70 (`fingerprints.py:15-18`), zero per-car tuning beyond `torqueV=500`. Upstream's own precedent (GM Yukon, `gm/interface.py:218`) is `dashcamOnly=True` for exactly this state.
2. **No firmware-version fingerprinting** — `values.py:7` imports `FwQueryConfig/Request/StdQueries` but never defines `FW_QUERY_CONFIG`. Every upstream brand has real UDS queries.
3. **`stockAeb = False` hardcoded** (`carstate.py:147`, "Todo: get the real value") — safety-relevant; upstream always reads a real signal.

**MEDIUM:** misplaced `tireStiffnessFactor` (hardcoded 0.7933 not in CarSpecs); non-standard 1-point `torqueBP/torqueV` convention + `assert(len==1)`; X90 magic constants (hardcoded gear=2, `steer_cmd*2`).

**LOW:** placeholder CAN-parser frequencies (all 0); stale `_get_params` signature; dead `else: dashcamOnly=True`.

**Structural fork-lag:** brand code in `selfdrive/car/` with `openpilot.selfdrive.car...` imports; upstream moved brand code to a separate `opendbc` package. Won't merge upstream without relocation.

**What Kommu 2 does right:** dataclasses + `Platforms` enum + `CarInfo` + `DBC` map + `CarControllerParams` + per-brand `fingerprints.py` + `protoncan.py` factoring — on-style for its era.

---

## 5. AI Model vs Controller (clarification)

Two separate systems, often conflated:
- **AI driving model** (Kommu's own `supercombo.onnx/.thneed/.rknn`, `modeld.py:30-32`): perception + path planning — decides *where* to go.
- **Lateral controller** (PID vs torque): steering — follows the path.

Independent layers. Upgrading a "model version" does NOT change which controller you use, and vice versa. The "v10/v11" model-version talk is unrelated to the PID-vs-torque question. (Could not confirm specific v10/v11 numbers from code.)

---

## 6. Key File References (KA2 / beta = working tree)

| File | What's there |
|---|---|
| `selfdrive/car/proton/interface.py:24,28-32,42,45,47-57` | shared tuning + per-car STEER_MAX |
| `selfdrive/car/proton/values.py` | CarSpecs (mass/wheelbase/steerRatio), CAR enum, DBC map |
| `selfdrive/car/proton/carcontroller.py:36-41,77,125-128` | rate limits, `driver_torque=0`, X90 cmd doubling |
| `selfdrive/car/proton/carstate.py:96,142,147,154` | stock_acc_cmd read, steeringPressed=65, stockAeb=False, gap button |
| `selfdrive/car/proton/protoncan.py:40-41` | ACC_CMD blend signals, SET_ME_X6A |
| `selfdrive/car/proton/fingerprints.py:15-18` | X70 placeholder fingerprint |
| `selfdrive/car/proton/radar_interface.py` | empty stub → radarless |
| `selfdrive/car/torque_data/{params,override,substitute}.toml` | torque controller seeds (NO Proton entry) |
| `panda/board/safety/safety_proton.h:8-23` | permissive no-op safety |
| `selfdrive/controls/controlsd.py:246` | torque/PID controller selection |
| `selfdrive/controls/lib/latcontrol_{pid,torque}.py` | both controllers exist |
| `selfdrive/locationd/{torqued,paramsd}.py` | lateral-only auto-learning |

KA1 equivalents: `git show FETCH_HEAD:<path>` (KA1 has no `safety_proton.h`).

---

## 7. Conclusions & Recommended Fixes

### Steering (the actual broken thing on KA2)
The "stronger / harder to override" feel = the **gains** (`kf` up 46×, `ki` up 2.5×), applied through an override path that has never provided gradual driver handoff. To restore KA1 feel:
1. **Port KA1 per-car PID back** — especially `kf` (0.0000015 for X50/S70/X90; 0.000006 for X70) and per-car `kiV`.
2. **X70:** also restore its unique `steerActuatorDelay` 0.13 and `steerRatio` 16.0.
3. (Optional) Investigate the inert driver-override (`driver_torque=0`) if easier override is desired — but that's a separate change, not the cause of the KA1→KA2 delta.

### Longitudinal
Working and reasonably designed (blend = safety hedge). Not the priority. If jerkiness appears, suspects are the magic blend numbers (×15/×18, the mult interp, 2.5 threshold).

### Detection
The X70 placeholder fingerprint (`# TODO`) is a separate problem from tuning — solve for correct car identification independently.

### Kommu dev input (noted)
- Latest code uses shared lateral tuning for all Proton cars because per-car tuning wasn't changing the feel (per dev). Our code review says `kf` IS the lever — likely the dev changed `kp/ki` not `kf`, or hit param caching.
- Dev recommends **PID over torque** for the X70 (tested both).
- KA1 lacked longitudinal due to hardware, not code.

---

## 8. Comma's official tuning methodology (from the openpilot wiki)

Source: `Tuning.md`, `Fingerprinting.md`, repo `docs/how-to/car-port.md`, `docs/INTEGRATION.md`, plus `latcontrol_torque.py`/`torqued.py`. **Key: the wiki documents the PID controller in detail but has NO torque-controller tuning page** (torque is comma's modern default, meant to be self-tuning).

### 8a. The `kf` smoking gun (comma's own words)
`kf` = feed-forward on **desired steering angle**, acts only in curves, scales with angle. Wiki symptoms of too-high `kf`: *"turn too hard, overshoot the correct curvature, or at the end of a curve have trouble straightening out"*; *"enters too early / rides too far inside → kf too high → lower in 10% increments."* **A 46× `kf` increase (KA2) is exactly this.** There's no separate "override strength" knob — override difficulty tracks commanded torque magnitude.

### 8b. Official PID tuning order (one param at a time, <10%, on a known road)
1. **`steerActuatorDelay` FIRST** — "vital… tiny adjustments have huge impact." Turn-in too early → decrease; too late → increase. **Not a PID-only knob:** it's consumed by the AI model (`modeld.py:187`: `steer_delay = steerActuatorDelay + .2`) and by `torqued` (`torqued.py:55`, same formula) — so it's the **shared first step for both PID and torque** controllers, and a wrong value also skews `torqued`'s learning. KA2 moved the X70's 0.13 → 0.30 — verify it.
2. Set `ki=0`. Seed `kf` from a similar car (or ~0.00001→0.00003), raise until it "almost makes turns but doesn't overshoot."
3. Set `kf=0`, tune **`kp`** — raise until it centers, then back off until no oscillation anywhere.
4. Set **`ki` ≤ 1/10 of `kp`** (smooths/holds center & turns; if it oscillates/ping-pongs, lower `ki`).
5. Reintroduce `kf`; curve-only oscillation → lower `kf`/`kp`; "turning too long / slow to straighten" → lower `ki`.

**The 46× `kf` jump skips every step of this.** Proper fix: revert `kf` toward KA1 value and re-tune by this procedure (= porting KA1's per-car PID back).

### 8c. Critical torque-controller caveat
**`torqued` self-learning is allow-listed.** On `beta` the list is only `['toyota','hyundai']` (`torqued.py:35`); on `staging` (0.10) it expands to `['toyota','hyundai','rivian','honda','volkswagen','dnga']` (`torqued.py:36`, now keyed on `CP.brand`). **Either way Proton is excluded** on both branches. So switching the X70 to the torque controller would:
- **NOT auto-learn** (Proton not allow-listed) → must hand-measure `latAccelFactor` (~1.5–3.5), `friction` (~0.08–0.4), `maxLatAccel`, add to `torque_data/params.toml`, AND add Proton to `ALLOWED_CARS` in `torqued.py`.
- Be a **much bigger effort** than fixing the PID `kf`.
Hard ISO ceiling on the torque controller: lateral accel ≤ 3.0 m/s², jerk ≤ 5.0 m/s³.

### 8d. Longitudinal (PI loop, no D)
Architecture: lead (camera/AI model — Proton is radarless) → Kalman → LongitudinalMPC (desired accel) → `longcontrol.py` PI loop. Generic order: set `ki`=0, tune `kp` (raise if too little gas/brake to reach speed or stop behind a lead; lower if jerky), then `ki` (raise for hills/sustained error; lower on overshoot, "most easily identifiable on hills"). Following distance/personality is set in the MPC (gap button), NOT in this PI loop. Diagnose with PlotJuggler + `longitudinal.xml` layout (desired vs actual accel, lead distance, speed error, `stock_acc_cmd`).

**Proton long values to tune (KA2 `interface.py`):**

| Value | KA2 generic | X50 override |
|---|---|---|
| `kpV` (kpBP=[0,5,20]) | `[0.7, 0.5, 0.4]` | `[0.5, 0.4, 0.3]` |
| `kiV` (kiBP=[0,5,20]) | `[0.2, 0.15, 0.1]` | `[0.05, 0.05, 0.05]` |
| `longitudinalActuatorDelay` | 0.4–0.5 s | — |
| `stopAccel` / `startAccel` | −0.8 / 1.2 | — |
| `stoppingDecelRate` | 0.3 | — |

(X70/S70/X90 use the **generic baseline** — no long-specific tune, same situation as their lateral.)

**Proton-specific extra levers — the blend (`carcontroller.py:137-145`):** because Proton blends with stock ACC, these are ALSO tunable and are unique to Proton (a clean openpilot car has none): `×15` (gas) / `×18` (brake) m/s²→Proton-unit scaling; `mult = interp(vEgo,[0,28.3],[1.0,0.6])` (stock influence by speed); the `2.5` m/s threshold (average vs min). The PI output is **mediated by this blend** before reaching the car, so long is harder to tune cleanly than lateral — the gains and the blend are coupled.

**Base-change caveat (same as lateral):** KA1 had ~7× higher long gains (`kpV=[5.0,3.6,3.6]`, `kiV=[1.5,1.0,1.0]`) — but that was classic-openpilot scale; the LongController changed in the modern base, so **don't blindly revert** KA1's numbers. Re-tune on KA2 if needed.

**Assessment:** KA2's long `kpV` (`[0.7,0.5,0.4]`) is in the normal modern-openpilot range (Toyota ~`[1.3,1.0,0.7]`), so **unlike lateral, long is probably fine as-is** — only tune it if a specific issue (sluggish, jerky, bad stops). If so, suspects = the blend numbers (×15/×18, `mult`) first, then the PI gains.

### 8e. Tools & workflow
- **PlotJuggler** — comma's recommended tuning tool. **It is NOT a PID calculator** — it's a third-party time-series *visualization* GUI (`https://github.com/facontidavide/PlotJuggler`, by Davide Faconti). It plots logged driving data so a human can measure `steerActuatorDelay` (overlay desired vs actual torque) and judge whether to raise/lower `kf`/`kp`/`ki` from the controller's P/I/F traces, oscillation, overshoot, saturation. It does NOT output gain numbers. (source: `https://github.com/commaai/openpilot/wiki/Tuning`)
- **openpilot ships first-party PlotJuggler integration:** helper `tools/plotjuggler/juggle.py`, comma plugin fork `https://github.com/commaai/PlotJuggler`, and **15 prebuilt layouts incl. `tuning.xml`** (plots `pidState/f`, `/p`, `/i`, driver-vs-EPS torque, plan-vs-achieved curvature, saturation). (source: `https://github.com/commaai/openpilot/tree/master/tools/plotjuggler`, layouts: `https://github.com/commaai/openpilot/tree/master/tools/plotjuggler/layouts`)
- **Install + use:**
  ```
  cd openpilot/tools/plotjuggler && ./juggle.py --install
  ./juggle.py "<route>" --layout=layouts/tuning.xml     # cabana share URL also accepted
  ```
  Log format = rlog (full-res) / qlog (smaller) capnp files; `juggle.py` converts & loads them. (source: `https://github.com/commaai/openpilot/blob/master/tools/plotjuggler/juggle.py`, logreader: `https://github.com/commaai/openpilot/blob/master/tools/lib/logreader.py`)
- **Tuning order with PlotJuggler** (source: `https://github.com/commaai/openpilot/wiki/Tuning`): (1) `steerActuatorDelay` first — overlay Driver vs EPS torque, read the lag, tiny steps; (2) `kf` (curves) — watch `pidState/f` + curvature, lower ~10% if turns too hard/overshoots; (3) `kp` (straight lane changes) — watch `pidState/p`, raise to crisp then back off at oscillation; (4) `ki` ≤ 1/10 `kp`, lower if ping-pongs; (5) re-check delay. Capture before/after plots for a tuning PR.
- **Tutorial video (verified, "Openpilot Plotjuggler demo" by Gregor Kikelj):** `https://youtu.be/DgcMeTpHZnE`
- **Heads-up:** `juggle.py` now says *"JotPluggler is the future… PlotJuggler will be deleted soon"* — comma is moving to a successor, but PlotJuggler is the documented tool today.
- **cabana** — CAN/DBC viewer.
- Loop: record drive → PlotJuggler to diagnose → change one param <10% → re-drive on a known road.
- **Fingerprinting 2.0** = firmware-version query (official makes); **1.0** = CAN-message-set (community/deprecated). `dashcamOnly` = fingerprint not matched → dashcam fallback.

> **⚠️ Kommu-specific caveat (verified in bukapilot repo):** bukapilot DOES ship the PlotJuggler tooling and `tuning.xml` (`tools/plotjuggler/juggle.py`, `layouts/tuning.xml`), so the workflow above works locally. BUT the comma cloud URLs (`connect.comma.ai`, `useradmin.comma.ai`) in the inherited code are **comma's** — Kommu routes are almost certainly **NOT** on them (Kommu uses its own backend; bukapilot README: *"uploads the driving data to our servers"*). Reliable data access on Kommu = **SSH to the KA2 device** and run `juggle.py` with the on-device route path (or pull the `rlog` to a laptop), or use **live stream mode** (`./openpilot/cereal/messaging/bridge` on device + `ZMQ=1 ./juggle.py --stream` on a laptop over the device WiFi hotspot). `juggle.py --install` pulls PlotJuggler from comma's **public** GitHub release (works). `juggle.py` needs the bukapilot Python env — easiest **on-device** or via streaming; full env setup on a personal laptop is heavy.

### 8f. Net recommendation (wiki-aligned)
1. **Restore the PID tune** — revert `kf` to ~KA1 value, re-tune by the §8b procedure (esp. `steerActuatorDelay` and `kp` first). This is the documented, low-effort fix for "too strong."
2. **Stay PID** — the torque controller is undocumented for tuning AND blocked from auto-learning for Proton. Corroborates the Kommu dev's "PID > torque for the X70."
3. Diagnose data-driven with PlotJuggler, not by feel.

---

## 9. ⚠️ SUGGESTED FIX (NOT applied) — revised after deeper check

> **Supersedes the "revert kf to KA1 value" wording in §7/§8f.** A follow-up check of the controller code changed the recommendation. Do **not** blindly revert to KA1's numbers.

**What the deeper check found (`latcontrol_pid.py`, `latcontrol.py`, `carcontroller.py` in both branches):**
- Output scaling is **identical** in KA1 and KA2: PID/PI output is normalized to **±1.0** (`latcontrol.py:17` `self.steer_max = 1.0`), then carcontroller does `actuators.steer × STEER_MAX` (`carcontroller.py:76` KA2 / `:67` KA1). So the 46× `kf` is a genuine ~46× feedforward increase, **not** a unit artifact.
- BUT the controller **class** changed: KA1 = `PIController`, KA2 = `PIDController`, with a different `update()` signature. And KA1's `kf = 0.0000015` is ~2 orders of magnitude smaller than comma's wiki example (`0.0002`), while KA2's `0.00007` is a typical modern-openpilot value.

**Why the Kommu dev most likely changed the values:** the classic→modern openpilot base migration **swapped the lateral controller implementation** (`PIController` → `PIDController`). KA1's tiny `kf` was tuned to the old controller/old hardware and doesn't port cleanly. They adopted the modern openpilot PID convention instead (shared baseline, `kf` ~0.00007). So the change was a **necessary base-migration step**, not arbitrary — the user's instinct ("there must be a reason") is correct.

**Suggested approach — TWO options to try (do NOT blindly revert KA1 numbers):**

**Option A — Fix the PID (recommended, lowest effort, dev's preference):**
1. Re-tune on KA2 via comma's procedure (§8b): PlotJuggler, one param at a time, `steerActuatorDelay` first.
2. **Lower `kf` in ~10% steps from 0.00007** until the "too strong" feel eases — NOT all the way to KA1's 0.0000015 (that was for the old `PIController`; would go too weak/dead in the new `PIDController`).
3. Then `kp`, then `ki` ≤ 1/10 `kp`.
4. Re-check `steerActuatorDelay` (KA2 moved the X70's 0.13 → 0.30 — verify with data).
- No seed, no toml, no torque controller involved — directly fixes the broken `kf`.

**Option B — Try the torque controller (more setup; bypasses `kf` entirely):**
The torque controller doesn't use `kf/kp/ki`, so switching sidesteps the broken PID gains. Steps (detail in §1e/§8c):
0. **Confirm `steerActuatorDelay` first** (same as Option A) — it's model-level, feeds both controllers, and a wrong value also skews `torqued`'s learning (`modeld.py:187`, `torqued.py:55`). For the X70, sanity-check the 0.13 (KA1) vs 0.30 (KA2) value.
1. **Seed** (one line, `substitute.toml`): `"PROTON X70" = "HYUNDAI SANTA FE 2019"` — borrow a similar SUV; just a starting estimate, not permanent.
2. **Enable auto-learning**: add `'proton'` to `ALLOWED_CARS` (`torqued.py:35`) so `torqued` MEASURES the real X70 `latAccelFactor`/`friction` from your driving (within ±30% of the seed). Without this, it's a frozen static tune (kommu 2's `ALLOWED_CARS` is only `['toyota','hyundai']`, so Proton is excluded by default).
3. **`interface.py`**: replace the X70 PID block with `CarInterfaceBase.configure_torque_tune(candidate, ret.lateralTuning)`.
- Tradeoffs: more moving parts; static until `torqued` converges; the Kommu dev tested it and **PID felt better** ("X70 already has good torque"). But valid if PID re-tuning stalls.
- Smart "from scratch" path = **borrow as seed → enable torqued → drive → torqued replaces borrowed values with real measured X70 ones**.

**Recommendation:** start with **Option A** (PID). Only try Option B if PID can't be made to feel right.

**Still unexplained (open mystery):** the dev's report that "changing the tuning values did not change the feeling" on KA2. The code clearly applies the gains, so this likely needs device-level debug (did they change `kp/ki` vs `kf`? is there a CarParams caching/reload issue?). Cannot be resolved from the repo alone.

---

## 10. How to apply changes on a Kommu device (deployment)

You can run your own edits **without write access to Kommu's repo** — the device's clone is a standard git repo (`origin = https://github.com/kommuai/bukapilot.git`, verified in `.git/config`). Three options, lowest-effort first:

1. **SSH direct edit (simplest, no git/fork needed).** SSH into the KA2 device, edit the files directly under the openpilot dir (typically `/data/openpilot`, standard openpilot layout), then restart openpilot (reboot the device, or `pkill` the manager so it respawns). For tuning changes — a few numbers in `selfdrive/car/proton/interface.py`, a line in `selfdrive/car/torque_data/substitute.toml`, or `'proton'` in `selfdrive/locationd/torqued.py` — this is the fastest path and needs **zero repo access**. **Catch:** edits are lost if the device updates/resets to its branch. Good for testing; snapshot your diffs locally.

2. **Your own GitHub fork (persistent, version-controlled).** Fork `kommuai/bukapilot` → `github.com/<you>/bukapilot` (free; you have write access there), push a branch with your changes, then point the device at your fork:
   - Via SSH: `git remote set-url origin https://github.com/<you>/bukapilot.git` → `git fetch` → `git checkout <your-branch>`.
   - Or, if Kommu's device exposes an installer-style switcher (comma devices use `installer.comma.ai/<user>/<repo>/<branch>`), enter your fork URL there.
   *(Caveat: I could not confirm Kommu ships comma's installer UI — but the SSH `git remote set-url` method always works since it's plain git.)*

3. **Branch-only (what you have now).** Switch between existing branches of `kommuai/bukapilot` via the device — but that only gives you what Kommu published, not your own edits.

**Recommendation:** use **option 1 (SSH direct edit)** to test tuning changes quickly; move to **option 2 (your own fork)** once you want them to survive updates. Neither requires any access to Kommu's repo.

---

## 11. End-to-end (E2E) driving status — comma vs Kommu

**Two senses of "E2E" — neither Kommu nor comma does the full one:**
1. **Planning-level E2E** — the AI model outputs a desired path/curvature (and recently desired accel) straight from the camera, but **classic controllers (PID/torque + MPC) still actuate** steering/brakes. ← This exists.
2. **Full E2E** — the model directly emits steering torque / accel, no controller in the loop. ← **Does not exist anywhere.** comma's own `docs/contributing/roadmap.md` lists *"fully end-to-end driving policy"* as the future **openpilot 1.0** goal.

So comma's marketing "end-to-end" = sense #1 (the *planner* is a neural net), **not** the model driving the car directly. Provable in code: the model `Action` struct carries only `desiredCurvature` (+ `desiredAcceleration` in newer bases), never a steering-torque field; torque is always computed by a lateral controller (`latcontrol_*.py`). comma's `rlcontrols` blog: *"a controller must apply steering wheel torque"* to achieve the model's curvature.

**comma (current, 2025–2026):**
- **Lateral E2E** (model emits curvature directly, lateral MPC planner dropped) — ships in **both Chill and Experimental** (~since 0.9.6). A PID/torque controller still converts curvature → torque.
- **Longitudinal E2E** via the **World Model** (0.10 Aug 2025; 0.11 Mar 2026) — **Experimental-mode only**. Chill longitudinal is still a classical lead policy; the lead-MPC also runs at runtime as a safety min-gate (`longitudinal_planner.py` `min()` of candidates). Full longitudinal E2E in Chill = the stated **openpilot 1.0** goal.
- Runs on **comma 3X** and **comma four** ($999, Nov 2025).
- Sources: [0.10 blog](https://blog.comma.ai/010release/), [0.11 blog](https://blog.comma.ai/011release/), [rlcontrols blog](https://blog.comma.ai/rlcontrols/), [releases](https://github.com/commaai/openpilot/releases).

**Kommu/bukapilot:**
- **Inherits openpilot's partial-E2E stack unchanged.** Base depends on branch: **`beta` (shipping) is pre-0.10** — its model `Action` struct has only `desiredCurvature` (no `desiredAcceleration`); **`staging` (bukapilot v10.1.0) = openpilot 0.10.3** — `Action` has `desiredCurvature` + `desiredAcceleration` + `shouldStop`, navd removed → it **does** have the World-Model longitudinal E2E (0.10 level; still behind comma's 0.11).
- Kommu's **"LONG MPC SOURCE = e2e"** indicator (what Kommu points to as "end-to-end") is just openpilot's **legacy Experimental/blended mode** (`long_mpc.py:380` sets `self.source = 'e2e'`), NOT the 0.10/0.11 World-Model policy.
- Kommu's *own* work is **not E2E**: KA2 hardware + RKNN NPU model runner, Proton/Perodua/BYD car ports, the stock-ACC longitudinal blend. **No Kommu-trained model** — ships the upstream `supercombo` (ported to RKNN).
- Sources: [kommu.ai](https://kommu.ai/), [bukapilot](https://github.com/kommuai/bukapilot).

**Bottom line:**
- **Full E2E: nobody** — both run model → plan → classic controller → actuators.
- **comma is ahead** (World-Model longitudinal E2E in Experimental; lateral E2E in Chill). Full E2E is the 1.0 goal.
- **Kommu base:** `beta` (shipping) is pre-0.10 / a generation behind; **`staging` (v10.1.0) caught up to openpilot 0.10.3** and is the active 0.10 work. Kommu's "e2e" label = openpilot's experimental mode (legacy blended on beta; real 0.10 World-Model E2E on staging).
- For the X70: E2E status doesn't change the steering/longitudinal tuning picture (§1, §2) — still PID + the blend, model provides the path plan.

---

## 12. Conditional Experimental Mode (CEM) — traffic-light stop, lead slowdown, the <30 km/h

The "traffic light stop / bump-car slowdown, only <30 km/h" feature is **Conditional Experimental Mode** — `selfdrive/controls/conditional_experimental_mode.py` (wired into `plannerd`). It **auto-toggles openpilot's Experimental Mode** (E2E longitudinal) on/off by conditions, so the smart stopping works without manual toggling. Traffic-light/stop-sign handling is officially an experimental-mode feature (`docs_definitions.py`).

**Conditions that turn experimental mode ON (`should_enable`):**
1. **Model stopping detected → "traffic light stop."** Model predicts low future speed (2s/4s/6s/10s) or strong decel — "sees" a red light / stop line / stopped traffic.
2. **Slow/stopped lead → "bump car slowdown."** Lead `<8.3 m/s (~30 km/h)` or closing fast (`vRel < -2.68 m/s`).
3. **Sharp curve ahead.**
4. **Low speed (<30 km/h)** — `LOW_SPEED_LIMIT = 30 * KPH_TO_MS`.

**Why <30 km/h:** `LOW_SPEED_LIMIT` sits between `CRUISING_SPEED=18` and `TURN_OFF_CEM_SPEED=110`. The code doesn't document a deeper reason; it's the author's chosen **city-speed threshold** — auto-enable the experimental E2E stopping only where it's useful (lights, queues) and low-consequence if it errs; above that, use the validated ACC. Not strictly "<30 only" — triggers 1–3 can enable it above 30 too (up to 110).

**Personality-gated** (enum: aggressive=0, standard=1, relaxed=2). On **beta**: aggressive(0) → CEM forces experimental OFF (**no corner-slowing even with CEM on**); standard(1) → curve/lead/model-stop; relaxed(2) → those OR below-30. **`staging` already fixed the aggressive-disable** — aggressive now keeps `curve_detected or slow_lead_detected` when CEM on (corner-slowing works), still no model-stop. See **§16** for the full corner/no-lead story + the verified DBC polarity + comma-vs-Kommu. Plus a master `ConditionalExperimentalMode` setting.

**Comma vs Kommu (the feature itself):** both have traffic-light/lead-stop (Kommu inherited it). Difference = **activation**: comma = you manually toggle experimental mode (works at any speed); Kommu = CEM auto-toggles it (on below 30 km/h / on detected stop/lead). The <30 is **Kommu/CEM-specific** (no such gate in comma). Comma's detection is newer (World Model) vs Kommu's older base. **CEM is a community-fork feature** (Sunny/FrogPilot lineage), not upstream comma or Kommu-original.

---

## 13. 0.10 / `staging` — the branch to base X70-on-0.10 work on

**Branch picture (verified `git ls-remote` + per-branch checks):**
- `beta` (shipping) = **pre-0.10** openpilot. `Action` struct = `desiredCurvature` only; `navd` present.
- `staging` = **bukapilot v10.1.0 = openpilot 0.10.3** (RELEASES.md: *"Bump to openpilot v0.10.3"*). `Action` = `desiredCurvature`+`desiredAcceleration`+`shouldStop`; `navd` removed. Single squashed commit `0dc8624 "bukapilot v10.1.0"` (release-prep, not a stub).
- Other 0.10 branches with Proton: `hons` (07-29), `umi` (06-18), `staging_v2`/`qc_tester` (05-19). **`staging` is the newest mainline → base here.**

**"1 commit" = squashed release commit + my shallow fetch** — not a sign it's experimental/empty. staging is a **complete, real 0.10.3 tree** (cereal/openpilot/opendbc_repo/panda/system/tools all present).

**Path changes on 0.10 (car code moved into the opendbc package):**
- Proton code: `selfdrive/car/proton/` → **`opendbc_repo/opendbc/car/proton/`**
- Safety: `panda/board/safety/safety_proton.h` → **`opendbc_repo/opendbc/safety/modes/proton.h`**
- Enum: `CAR.X70` → **`CAR.PROTON_X70`**; torqued uses `CP.brand` (was `CP.carName`)
- `torqued.py` + `conditional_experimental_mode.py` still under `selfdrive/`

**beta → staging: all important Proton code present (nothing critical dropped), some refactored/improved:**

| Code | Status on staging vs beta |
|---|---|
| `interface.py` X70 tuning | **identical** (`kf=0.00007`, delay 0.30) — tuning fix still needed |
| `carcontroller.py` blend | **intact, refactored** — `×15`→`ACCEL_POSITIVE_SCALE`, `×18`→`ACCEL_NEGATIVE_SCALE` (same values); `driver_torque=0` at call site **unchanged** |
| `carstate.py` | intact; `steeringPressed` now **two thresholds** (124 & 65); `stockAeb=False` unchanged |
| Safety `proton.h` | **improved** — real `controls_allowed` true/false gating (beta was always-true no-op) |
| `torqued.py` allow-list | **expanded** to 6 brands — **Proton still excluded** (torque auto-learn still off) |
| `fingerprints.py` X70 | **still `# TODO` placeholder** |
| CEM `<30 km/h` | intact |

**X70 gaps UNCHANGED on staging (your to-do carries over):** placeholder fingerprint, no torque auto-learn, same `kf`/delay tuning, inert `driver_torque=0`. The 0.10 rebase did **not** fix these.

**Usability:** staging is **good for X70 dev on 0.10** (complete, current, you get World-Model longitudinal). It's a **moving pre-release target** (single squashed commit, active dev) — for daily-driving stability wait until it graduates to a `release_ka2 v10.1.x`.

---

## 14. Longitudinal stop-and-go — KA1 vs beta regression, and tuning openpilot-long properly

User observations on **beta** (openpilot-long, the default): slow restart from stop; late/hard stop at ~30 km/h when the lead stops; brake pulsing/disc noise when stopped; following distance a bit far. Decision: **stay on openpilot-long and tune it properly** (not stock-acc).

### 14a. KA1 (`snapshot`) vs beta — code-confirmed differences (user's "KA1 felt better" is real)
- **Resume logic:** KA1 gated the SNG resume on **`lead_moved`** (`carcontroller.py:119`) — computed from `CS.leadDistance` as *"valid lead AND lead distance increasing (lead driving off)"* — sent the resume signal *when the lead car actually moved* (`:136`). **Beta removed all lead logic** (carcontroller has zero `lead_moved`/`leadDistance`/`dRel`/`vRel` references) and **replaced it with a fixed ~3s timer** (`actuators.accel > 0 and frame > sng_next_press_frame`) — no lead check. → the "slow / not lead-aware restart."
- **Same car, same lead source:** `radar_interface.py` is an empty stub in **both** KA1 and beta — openpilot gets leads from the vision model via `CS.leadDistance` in both. So beta **has the same lead data available**; it just doesn't use it. **Porting `lead_moved` back is straightforward** (the signal exists in beta).
- **Standstill hold / brake pulsing — KEY: KA1 was stock-acc, not openpilot-long.** KA1's `create_acc_cmd` is **commented out** (`carcontroller.py:109`) — KA1 only sent **steering + SNG resume buttons**; the **Proton's own stock ACC held the stop** (clean, no pulsing). **Beta is openpilot-long (the blend)** — it holds the stop by sending continuous accel (`stopAccel=-0.8`) **averaged with `stock_acc_cmd`** at low speed → the brake **pulses/chatters** (disc noise). **So KA1's clean stop is NOT reproducible in openpilot-long** — it came from the factory ACC, not a better openpilot algorithm. (`keep_standstill` was **tried AND disabled in KA1 too** — `keep_standstill=False`, comment *"does not work... cannot make car move when lead moves"* — so it is **not** a viable fix.) beta's forced `STATIONARY=0`/`STANDSTILL_REQ=0` avoids the **EPB (parking brake)** engaging on resume (beta comment) at the cost of the command-brake-hold pulsing.
- **`SNG_WAIT`**: KA1 = named const `310`; beta = hardcoded `310`. **No comment explains why 310** — it's a tuned magic number.

### 14b. "The dev had reasons" — honest caveat
**For `lead_moved`: NOT a hardware/capability reason.** Same car, same `leadDistance` in both — beta simply dropped the logic (likely in the carcontroller rewrite for the blend/modern base). The data is there, so it can be restored; the reason it's gone isn't in the code (deliberate-vs-accidental unknown). **For `keep_standstill`: dead end** — KA1 already disabled it (`False`, *"does not work"*); **not a viable port.** The real difference is **KA1=stock-acc (Proton held the stop) vs beta=openpilot-long (the blend holds it)**.

### 14c. `startAccel = 1.2` — what it is
**Standard openpilot param.** It's **only the initial accel to break away from standstill** (get the car moving from 0). Once rolling, the normal planner + LongControl take over speed/following. "Start at 1.2, then openpilot handles the rest." Raise it (→ ~1.6–2.0) for a quicker pull-away; it is **not** an ongoing speed setting.

### 14d. "Why doesn't it slow down EARLIER when the lead brakes?" — reaction timing, controllable
Root causes (Proton openpilot-long) — **all tunable/improvable** (openpilot is good; comma's radarless cars stop fine, so this is not a hard limit):
1. **Following distance too far** (main lever). A large target gap means it doesn't react until the gap closes → coasts then brakes late. **Closer gap = earlier reaction.**
2. **Radarless → vision-only lead detection** adds *some* latency/noise vs radar, but it is **not** the dominant issue (comma's radarless cars are smooth) — a newer model (staging 0.10 World Model) improves it.
3. **Long PID** (`kpV=[0.7,0.5,0.4]`, `kiV=[0.2,0.15,0.1]`) only *tracks* the planner's desired accel → affects smoothness, **not** reaction timing.
4. **Actuator-delay model** (0.4–0.5s) + the blend.

**The longitudinal "style" knobs** — all in `selfdrive/controls/lib/longitudinal_mpc_lib/long_mpc.py`, **global / shared** (the MPC is the **same for every car** — Proton, Toyota, BYD all use it; a car has no own `long_mpc`). Per-car longitudinal lives in `interface.py` (long PID `kpV`/`kiV`, delays) + `carcontroller.py` (blend), not here.

| Knob | What it changes | For your goal |
|---|---|---|
| **`T_FOLLOW`** (seconds) | following **distance**, speed-scaled part (`T_FOLLOW × speed`) → how **soon** it reacts | ↓ lower = react sooner / lift off earlier (proactive) |
| **`STOP_DISTANCE`** (meters) | **fixed minimum gap** floor (added on top; = the gap held when stopped) | ↓ lower = follow closer (esp. crawling/stopped) |
| **`jerk_factor`** | how **abruptly** accel changes (multiplies the MPC's jerk/accel-change cost) | ↑ raise = gentler, less "stab" |
| **`COMFORT_BRAKE`** (m/s²) | assumed comfortable **decel** → how much room it keeps (`v²/2×COMFORT_BRAKE`) | ↓ lower = more conservative / earlier / gentler |

(`safe_obstacle_distance = v²/(2×COMFORT_BRAKE) + T_FOLLOW×v + STOP_DISTANCE`.) ⚠️ Because these are global, changing them affects **all** Kommu cars, not just the X70.

**Kommu vs upstream comma** (Kommu customized these — not stock):

| | Kommu (standard) | comma (standard) |
|---|---|---|
| `T_FOLLOW` | **1.25s** | 1.45s (Kommu **closer**) |
| `jerk_factor` | **0.5** | 1.0 (Kommu **snappier**) |
| `COMFORT_BRAKE` | **2.5** | 2.5 (**same**) |
| `STOP_DISTANCE` | **5.5m** | 6.0m (Kommu **closer**) |

(personality range on Kommu: `T_FOLLOW` aggressive 1.20 / standard 1.25 / relaxed 1.40.)

→ **"Lift off when the lead slows" (proactive) = lower `T_FOLLOW`.** **Gentler braking = raise `jerk_factor` and/or lower `COMFORT_BRAKE`.** Timing and firmness are **independent** knobs. Note: Kommu already set `T_FOLLOW` closer and `jerk` snappier than comma, so you'd be tweaking already-customized values. The fix is **react sooner** (lower `T_FOLLOW`) + optionally **softer** (jerk/COMFORT_BRAKE), **not** the long PID.

### 14e. Proper openpilot-long tuning order for the X70 (committing to openpilot)
1. **Following distance** — set the gap closer (personality) so it reacts earlier. Likely fixes most of the "late reaction."
2. **Long PID** (`kpV`/`kiV` in `interface.py:34-39`) — raise for faster reaction if still sluggish; lower if jerky. (X50 already overrides lower; X70 uses generic.)
3. **Restart** — raise `startAccel` (1.2 → ~1.6–2.0); optionally port KA1's `lead_moved` (test it).
4. **Stop smoothness / brake pulsing** — keep openpilot-long (blend) for all moving/following cases; **only when stopped (standstill), forward the STOCK command** (`accel_cmd = CS.stock_acc_cmd`) so the **Proton's native ACC holds it** (that's what KA1 did — clean, no pulsing). Do **not** send openpilot's own accel when stopped (that's what pulses). `stopAccel`/`stoppingDecelRate` tune openpilot's moving-case hold. (**Not** "port `keep_standstill`" — that was tried in KA1 and disabled.)
5. **Diagnose** each with PlotJuggler + `longitudinal.xml` (desired vs actual accel, lead distance/speed), one change at a time.

### 14f. Would staging (0.10) help these?
**Partly.** The World Model's better lead/stop prediction may reduce the "late reaction" (14d #1/#2). But the **restart timer, brake pulsing (forced `STATIONARY=0`), and following distance are Proton-port artifacts that persist on staging** (blend + signals carried over unchanged, §13). So staging improves the model side, not the Proton-port side.

**Verified: staging's SNG = SAME as beta** (`SNG_INITIAL_PRESS_DELAY_FRAMES=310` + `actuators.accel > 0` + `not brakePressed`; **zero lead references**). Moving to 0.10 does **not** restore `lead_moved`. Staging also tweaked the blend scale: `ACCEL_POSITIVE_SCALE` 15→**16**, `ACCEL_NEGATIVE_SCALE` 18→**23**.

### 14g. Why the `actuators.accel > 0` resume is slow — and how standard openpilot does other cars
**Two causes on the Proton:**
1. **Config: the 310-frame (~3s) hardcoded wait** (`SNG_INITIAL_PRESS_DELAY_FRAMES`). This is a Kommu/Proton-specific constant — **standard openpilot has no such fixed SNG delay.** Lowering it is the most direct fix for the slow restart.
2. **Architecture: the Proton uses pcmCruise-style button injection** (it fakes the RESUME button because the stock ACC latches at standstill). Button-injection resume is inherently a step slower than direct accel, plus it waits on the planner to command `accel > 0` (lead-aware, but with vision-lead latency).

**How openpilot handles SNG resume for other cars (verified in upstream):**
- **Full openpilot-long cars (Toyota, Hyundai, …)**: resume = the planner commands `accel > 0` → **directly drives gas/brake** → car moves. **No button, no fixed timer.** As fast as the planner decides to go (immediate + lead-aware). `autoResumeSng` enables auto-resume.
- **pcmCruise / stock-cruise cars (Honda)**: openpilot sends a `RES_ACCEL` button (`carcontroller.py:192`) when `cruiseControl.resume` is set — button injection like Proton, but **driven directly by the planner/driver, with no arbitrary 310-frame wait.**
- **Proton (Kommu)**: pcmCruise-style button injection **+ a Kommu-added 310-frame delay + (KA1) `lead_moved` / (beta/staging) `accel > 0` trigger.** The 310 and the trigger logic are Proton-specific — standard openpilot doesn't need them because full-long cars just command accel.

**So the slowness is mostly the 310 config + the button-injection architecture, not a fundamental limit.** Full-openpilot-long cars resume immediately; the Proton pays the 310 + button-dance tax because it rides on the stock ACC.

### 14h. Speed-dependent longitudinal — "reactive low / smooth high" + comma vs Kommu
**The idea (user):** be **more reactive at low speed** (stop-and-go, like a human) and **smoother at high speed** (more margin). Sound + safety-aligned (low-speed mistakes are low-consequence; high-speed ones costly).

**How speed currently affects longitudinal:**
- **Shared (comma + Kommu):** long PID gains vary by speed (`kpBP`/`kiBP` breakpoints). `T_FOLLOW` is **fixed per personality** → constant *time* margin across speeds, so openpilot does **not** naturally do "more reactive at low speed" in time terms — only the *distance* scales with speed.
- **Kommu-only (NOT in comma):** **CEM** (auto-switch to Experimental/E2E below 30 km/h — community-fork; comma's experimental mode is manual) + **the blend** (`mult = interp(vEgo,[0,28.3],[1.0,0.6])` + the 2.5 m/s avg/min threshold — Proton-specific). So Kommu has **more** speed-dependent machinery than comma, but it's Kommu-specific layers, not comma-standard.

**Two implementation paths:** (1) custom speed-dependent `T_FOLLOW` (e.g. 1.0s <30 km/h, 1.25s above) — code change, global; (2) tune existing mechanisms (CEM, blend `mult`, personality) — easier.

**⚠️ Needs more exploration — not yet clear how to use this for concrete improvements.** The concept is sound, but which lever (speed-dependent `T_FOLLOW` vs CEM/blend tuning) actually improves low-speed reactivity without jerk needs real-car testing. Flagged as a **research item, not an approved change.** Caveat: low-speed hyper-reactivity needs good lead detection (vision is noisier up close) to avoid jerk.

### 14i. Following distance (#4) — "feels far at high speed, OK at low" + how the Proton button is used
**Why far at high speed but OK at low:** gap = `T_FOLLOW × speed + STOP_DISTANCE`.
- Low speed (10 km/h): `1.25 × 2.8 + 5.5 ≈ 9 m` → `STOP_DISTANCE` (5.5 m) dominates → feels OK.
- High speed (100 km/h): `1.25 × 27.8 + 5.5 ≈ 40 m` → `T_FOLLOW × speed` dominates → feels far.
- ⇒ **lowering `T_FOLLOW` tightens high speed much more than low speed** (cuts ~7 m at 100 km/h, ~0.7 m at 10 km/h) → a *targeted* fix for "far at high speed" without making low speed too tight.

**How the X70's 3 distance settings are used (reinterpreted):** the button flows `SET_DISTANCE` (CAN) → `carstate.py:154` reads `distance_val` → `set_long_personality(distance_val - 1)` → writes `LongitudinalPersonality` param → planner reads it → `get_T_FOLLOW(personality)` → MPC. So the button is **borrowed as a personality selector**; openpilot uses its **own** `T_FOLLOW`, not the Proton stock gap:

| Proton button | openpilot personality | T_FOLLOW |
|---|---|---|
| 1 (closest) | aggressive | 1.20s |
| 2 | standard | 1.25s |
| 3 (furthest) | relaxed | 1.40s |

- **openpilot-long mode:** button → openpilot `T_FOLLOW` (above) — why it can feel further than "proton ori" at high speed (even closest = 1.20s ≈ 39 m at 100 km/h).
- **stock-acc mode:** openpilot doesn't plan longitudinal → `T_FOLLOW` unused → the **Proton's own** stock ACC reads the button and uses its native gap.
- **Global vs per-car:** button-reading is Proton-specific (`carstate.py`); the `T_FOLLOW` values + personality mapping are **global** (`long_mpc.py`, all Kommu cars).

**Lever for "feels far at high speed":** lower `T_FOLLOW` (e.g., aggressive 1.20 → ~1.0) in `get_T_FOLLOW`. `STOP_DISTANCE` (5.5 m) is the low-speed/crawling lever. **Needs real-car testing** to pick values that feel right.

---

## 15. Longitudinal — simplified: the issues + what to do next

**Your reported issues (recap):**
1. **Slow restart from standstill** — takes time to accelerate after stopping.
2. **At ~30 km/h, stops a bit late + brakes a bit hard** when the lead stops (vs Proton stock).
3. **Brake pulses / brake-disc noise when fully stopped** (feels like brake pressed repeatedly).
4. **Following distance feels a bit far** (maybe).

**Issue → cause → fix:**

| # | Issue | Root cause | Fix |
|---|---|---|---|
| 1 | Slow restart | 310-frame (~3s) wait + button injection + `accel>0` trigger | **Lower `SNG_INITIAL_PRESS_DELAY_FRAMES` (310 → ~100–150)**; raise `startAccel`; optionally port `lead_moved` |
| 2 | Late + hard stop | reaction timing (vision lead + gap + long PID); blend `min()` lets brake exceed stock | **closer following gap**; tune long PID; soften `stopAccel` (-0.8→-0.6) / cap blend brake |
| 3 | Brake pulsing when stopped | **KA1=stock-acc (Proton held it); beta=openpilot-long holds via continuous brake (blend)** | **Use STOCK when stopped**: when standstill, forward `CS.stock_acc_cmd` so the Proton holds natively (like KA1); keep openpilot-long (blend) for moving. Safer than sending openpilot's value. **Diagnose first.** |
| 4 | Following distance far | MPC personality/gap | **closer gap** (personality) |

**Do this next — ordered by ease/impact (simplest first):**
1. **Lower the 310 wait** — `SNG_INITIAL_PRESS_DELAY_FRAMES = 310` → ~100–150 in `carcontroller.py`. Biggest win for the slow restart; safe (just a faster resume). This is the headliner fix.
2. **Closer following distance** — gap button → aggressive personality. Fixes issues #2 (late stop) and #4 (far distance) together; **no code change, just a setting.**
3. **Raise `startAccel`** — 1.2 → ~1.6 in `interface.py`. Quicker pull-away after the resume fires.
4. **Port KA1 `lead_moved`** — smarter, lead-aware resume (replaces the timer+accel trigger). `leadDistance` exists in beta, so it's a clean re-add; test it.
5. **Brake pulsing — "use STOCK when stopped"** (corrected; user's insight): keep openpilot-long + the blend for **all moving/following** cases; **only when stopped (standstill), forward the stock command** (`accel_cmd = CS.stock_acc_cmd`) so the **Proton's native ACC holds it** — that's what KA1 did (clean, no pulsing). Do **not** send openpilot's own accel when stopped (that pulses). This is also **safer** than sending openpilot's value (the ECU trusts the stock command). **Diagnose first** (log ACC_CMD when stopped). (`keep_standstill` is a **dead end** — KA1 disabled it; "send openpilot's pure accel when stopped" was an **inverted** earlier idea — corrected.)
   - **Long-stop resume still works:** the SNG resume "magic" is `send_buttons` → **`ACC_BUTTONS`** (bus 2), a **separate CAN message** from the accel command (`ACC_CMD`, bus 0) that this fix changes. The resume trigger (`actuators.accel > 0` + timer) reads the **planner**, not the sent accel. So: stopped→forward stock; lead moves→planner wants accel→fake RESUME (`ACC_BUTTONS`)→stock releases standstill→car moves→openpilot switches back to blend. Forwarding stock also keeps the stock ACC **engaged** (closer to KA1), so it's *less* likely to fully cancel after a long stop.
6. **Tune the long PID** — `kpV`/`kiV` in `interface.py` for reaction speed vs jerk. Diagnose with PlotJuggler + `longitudinal.xml`.

**Approval status:** only **#1 (lower 310)** is approved so far. #2–#6 are **discussion candidates** (each should be discussed/understood before implementing — e.g. `startAccel` and the long PID haven't been properly discussed yet). **Safe/easy:** #1, #2 (gap). **Needs careful testing/diagnosis:** #4 (`lead_moved`), #5 (no-blend-when-stopped — diagnose first).

**Rule:** one change at a time → drive → observe (PlotJuggler to diagnose). All on `beta` (or `staging` — same SNG logic). Branch off `beta`/`staging`, edit, test (§10 for how to deploy).

---

## 16. Corner / no-lead behavior — "does it slow for corners?" + CEM owns Experimental Mode (added 2026-08-02)

Distinct from the stop-and-go issues (§14–§15): this is **no-lead, cornering** — where Proton stock ACC is fine, but does *openpilot* slow itself?

### 16a. Core mechanism — openpilot does NOT slow for corners by default
- **Standard ACC mode = holds your set speed, just steers. No corner slow.**
- Corner-slowing only happens in **blended (experimental)** mode, where the planner follows the model's predicted velocity.
- The two MPC modes (`long_mpc.py`), flipped by `longitudinal_planner.py:95` (`mode = 'blended' if experimentalMode else 'acc'`):
  - **`acc`** (experimental OFF): model velocity **zeroed** (line 366). Only lead + a fake cruise obstacle. Only corner effect = `limit_accel_in_turns` (planner:34-46), which caps *acceleration* (not speed) via a combined-accel budget — it can't make you brake.
  - **`blended`** (experimental ON): model velocity integrated into path `x`; follows `min(model path, cruise path)` (line 377-380). Model predicts slower for the curve → car slows.
- **"blended" is comma's term** (openpilot core, not a fork addition). `acc` = pure adaptive cruise (model only *finds* the lead); `blended` = ACC blended with the model's e2e trajectory (follows slowest/safest of lead/cruise/model; `source` reports `lead0/lead1/cruise/e2e`).

### 16b. Speed sources — "model proposes, code disposes"
Target speed has 3 sources: cruise setpoint (you), lead (vision), AI trajectory (`modelV2.velocity.x`).
- `acc`: cruise + lead only (model velocity discarded).
- `blended`: cruise + lead + model velocity (`min` of model & cruise).
- MPC then computes the pedal command, smoothing via comfort/jerk/`T_FOLLOW`.
- **For corners: the "slow" signal originates ONLY in the model. The mode decides whether to listen.** (Your understanding, confirmed.)

### 16c. Brake force in a high-speed corner (verified constants)
| Mode | Decel floor | Max accel | Turn-limiting? |
|---|---|---|---|
| `acc` | **-1.2 m/s²** (`A_CRUISE_MIN`) | 1.6→0.6 by speed | yes — caps combined accel (~3.2 @ 40 km/h) |
| `blended` | **-3.5 m/s²** (`ACCEL_MIN`) | +2.0 | **no** — full range even mid-corner |

- Blended can brake ~3× harder. Brake force = MPC tracking the model's target, bounded **[-3.5, +2.0]**, smoothed by jerk. It **scales to need** (gentle curve → small; tight high-speed → up to -3.5). "Gentle normally, harder when good" is **emergent**, not a dedicated knob.
- `COMFORT_BRAKE=2.5` mostly affects *lead-gap* planning distance, not corner speed.
- **Honest limitation:** no tire-limited combined-slip (brake-in-turn) physics guard in the longitudinal planner; relies on the model not asking for anything reckless + the driver.
- **Tunability (not a single "corner brake %" knob):** levers = `ACCEL_MIN` (-3.5), `jerk_factor` (personality, coupled to following distance), `COMFORT_BRAKE` (2.5). Biggest lever = whether blended is active at all.

### 16d. CEM is the SOLE writer of Experimental Mode in this fork (verified)
- Repo-wide grep: only `conditional_experimental_mode.py:113` writes `ExperimentalMode`. No manual/UI toggle in `selfdrive/` or `system/ui/`.
- CEM always runs (`plannerd.py:44`, no guard).
- **Implication:** in this fork, experimental mode is entirely CEM-controlled — there's no independent manual toggle that sticks (CEM would override it within 4 frames). Earlier claim "CEM off = purely manual" was **wrong** — corrected. (comma is the opposite: experimental = manual UI toggle, no CEM.)

### 16e. Personality ↔ Proton distance button (DBC-confirmed polarity)
- DBC `VAL_ SET_DISTANCE 3="3BAR" 2="2BAR" 1="1BAR"` → **1BAR=closest, 3BAR=farthest**.
- `carstate.py:154-155`: `set_long_personality(distance_val - 1)` → **1BAR→aggressive(0), 2BAR→standard(1), 3BAR→relaxed(2)**.
- Conceptually different (Proton's native ACC gap vs openpilot's driving-style personality); Kommu bridges them. comma has no Proton — personality is a device-UI toggle, independent of any car button.

### 16f. The coupling (questionable) — and comma's cleaner split
- On **beta**: aggressive(0) → CEM forces experimental OFF → **no corner-slowing even with CEM on**. This couples *following-distance* with *auto-intervention* — debatable (a close-follow driver on a winding road wants corner-slowing most; and it's invisible to the driver).
- **comma has no CEM** (404 verified on commaai/openpilot master). comma = personality (UI, gap+smoothness only) **+** separate manual experimental toggle. No coupling. That separation is cleaner.

### 16g. ⚠️ KEY FINDING — `staging` already fixed the aggressive-disable
The "obvious fix" you wanted is **already shipped on staging**:
- **beta:** `if (personality_type == 0) or not self.cem_enabled: should_enable = False` → aggressive always disabled.
- **staging:** `if (personality_type == 0): if not self.cem_enabled: should_enable = False` → with CEM on, aggressive keeps `should_enable = curve_detected or slow_lead_detected` → **corner-slowing works for aggressive.**
- staging also adds: `desiredCurvature` fallback when `curvature==0` (divide-by-zero guard for RKNN/straight roads), `float64` dtype, `constants` import (0.10 refactor).
- Note: on staging, aggressive still does **not** get `model_stopping_detected` (stop-light slow) — only curve + slow-lead. A deliberate middle ground (aggressive gets corner-slowing but stays less intervening than standard). Reasonable.
- **Net: basing on `staging` = no code change needed for this issue. Basing on `beta` = port staging's block.**

### 16h. Branch reality (your clone, 2026-08-02)
- Clone `github.com/lutfime/bukapilot` defaults to **`snapshot` (KA1)** — which has **no CEM file at all**.
- CEM exists only on `origin/beta` and `origin/staging`.
- So any corner-slowing/CEM work **must** be based on `beta` or `staging`, not `snapshot`.
