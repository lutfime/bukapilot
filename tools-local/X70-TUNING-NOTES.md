# X70 PID Lateral Tuning Notes

## Current State (2026-08-09, branch `x70-map`)
- **Controller:** PID (default; torque opt-in via toggle)
- **LAT_SMOOTH_SECONDS:** 0.2 (0.10 model path; raised 0.15→0.2 on 2026-08-09 to reduce stair-step) / 0.0 (0.11 path)
- **Experimental mode:** follows UI toggle (CEM override disabled — comma-style, always-on when toggle is on; see § "Experimental/CEM")
- **kpV:** [0.0005, 0.02, 0.045, 0.10, 0.17] at [0, 5, 15, 25, 35] m/s
- **kiV:** [0.001, 0.01, 0.09, 0.4, 0.5]
- **kf:** 0.0000250 (raised from 0.000006 on 2026-08-09; see § "Lateral kf" below)
- **steerRatio:** 20.0 | **steerActuatorDelay:** 0.17 | **STEER_MAX:** 580

## Known Issue: PID Steer Step Size (jerky feel)
**Symptom:** The PID makes discrete jumps in the steer command (mean 0.11, p95 0.34, max 0.36 normalized). Torqued felt smoother (continuous latacc-based output).

**Cause:** Each camera frame (~10-20 Hz) produces a new desired_curvature → the desired_angle jumps → `kp × Δerror` = a big step. With kp=0.10 and a 3° error jump: `0.10 × 3 = 0.30` → matches measured p95.

**Tracking is fine** (error ~0) — the issue is feel (discrete jumps), not accuracy.

## Do NOT (confirmed bad approaches)
- **Reduce kp:** Slows response → understeer on corners. Same tracking requires keeping kp.
- **Rate limiter:** Caps max change per tick → delays response in sudden maneuvers. Bad.
- **f4ce743 "upstream port":** Broke modeld (numpy 1.x incompatibility). Do NOT re-apply without testing on numpy 1.26.4.

## Approaches to Reduce Step Size (same response + tracking)
1. **LAT_SMOOTH increase (0.15 → 0.2-0.3):** Smooths the model's desired_curvature BEFORE the PID → smaller error jumps → smaller steps. Same kp → same response. Cost: slight setpoint delay (0.15-0.3s). **Simplest knob — already wired.**
2. **Derivative (D) term:** Adds damping → reduces overshoot/oscillation → smaller steps. Same kp/ki → same response + tracking. The CLASSICAL control answer. Need to check if openpilot's PIDController supports kd.
3. **Better feedforward (kf):** If ff provides more of the steering torque, the PID's P-term has less residual error → naturally smaller steps. Same response. Requires kf tuning.

## Root Cause: f4ce743 Broke modeld (2026-08-07 to 2026-08-08)
- The f4ce743 commit ported 5 files from comma upstream (drive_helpers, latcontrol_torque, lagd, paramsd, torqued).
- Upstream code assumes numpy >=2.0. Device runs numpy 1.26.4 (C++ extensions compiled against 1.x — upgrading to 2.x breaks visionipc_pyx).
- lagd crashed (`np.ptp` on empty array). drive_helpers (imported by modeld) silently failed → modeld hung → 0 modelV2 → blue LED.
- **Fix:** Reverted all 5 files to pre-f4ce743 state. Confirmed working.
- **Lesson:** Never port upstream code without testing on the device's actual numpy version (1.26.4).

## Device Constraints
- numpy 1.26.4 (MUST stay — C++ extensions compiled against it)
- uv.lock says 2.4.1 but device was built with 1.26.4
- rknnlite NOT installed (was for 0.11 model, which is too slow on this NPU)
- 0.11 supercombo model: loads but inference too slow (drops every frame) → stays on 0.10

---

# Lateral kf — corrected root cause + change (2026-08-09)

**Corrected architecture (read from device code, branch `staging-x70fix`):**
- Proton lateral command = `actuators.torque` (= PID output p+i+f) × `STEER_MAX` (580) → CAN steer. `actuators.steeringAngleDeg` is set by controlsd (controlsd.py:134) but **UNUSED** by this car (carcontroller.py:72 uses `torque`).
- Lateral PID is `openpilot/common/pid.py` (NOT `opendbc_repo/.../car/common/pid.py`, which is the car longitudinal PID). Its `update` does `self.f = feedforward` (kf baked into `ff` upstream).
- Feedforward: `latcontrol_pid.py:34` → `ff = kf * angle_des * vEgo²` (`get_steer_feedforward_default = angle × vEgo²`). So **`pidState.f = kf * angle_des * vEgo²`**.

**Real root cause (drives 2026-08-08--13-44-09 / 12-19-52, full-rate rlogs):** the "jerky feel" is **NOT step-size** — at 100 Hz output steps are small (mean 0.005, p95 0.023 norm). The issue is **integral dominance**:
- `f/OUT` = 2–8% (feedforward nearly absent), `i/OUT` = 82–95% (integrator carries almost all steer torque).
- → output is smooth but **laggy/floaty**: ramps late then overshoots. That's the "corners late" feel.
- Regression `|output|` vs `(angle_des·vEgo²)` → effective gain ~3.2e-5 vs kf 6.5e-6 ≈ **5× headroom**.

**Change applied:** kf `0.000006` → **`0.0000250`** on device line 85 (8-space indent preserved; backup `interface.py.bak-kf`). Goal: `f/OUT` 5% → ~15-25%, `i/OUT` drops toward ~60%.

**RESULT (measured 2026-08-09, post-kf drives 04-01-58 + 05-45-16, 8 full-rate rlog segs, `pid_analyze2.py`):**
- `f/OUT`: 2–8% → **~21%** (median 20.7%, range 11–30%) → **target 15–25% ACHIEVED.** Late-steer gone (user-confirmed).
- `i/OUT`: 82–95% → **~70%** (median, range 53–78%) → dropped toward ~60% but still the dominant term.
- Per-seg f/OUT/i/OUT: 29.6/67 · 20.7/78 · 17.5/53 · 11.1/58 · 28.4/59 · 23.6/73 · 20.3/75 · 20.7/76.

**kp verdict (the "should we restore the cut?" question):** the cut (commit `a11fb76`: kpV 0.06→0.045 @54km/h, 0.14→0.10 @90km/h) was to damp overshoot from kp × laggy integrator. With i/OUT still ~70% (not negligible), the cut is STILL doing useful damping → **leave kp as-is; do NOT fully restore.** f/OUT is in-target and the car feels good, so there's no complaint to fix by raising kp. If tighter mid-corner tracking is wanted specifically: half-restore (0.052/0.12), one drive, watch overshoot. Full 0.06/0.14 not justified while i/OUT ~70%.

**User partial bump (2026-08-09):** set kpV 54km/h `0.045→0.05` only (≈⅓ of the cut restored; 90/126 km/h unchanged). Tested on city drive `2026-08-09--10-30-57` (17 rlog segs): **drive was the wrong regime** — 86% of time <36 km/h, only **0.3% above 54 km/h** → the bumped breakpoint was active ~3 s, can't validate the change. NO regression seen: steady-state step p95 0.0263 @10-15 m/s (≈ baseline 0.0265), f/OUT 20.3% on the one ~42 km/h segment (seg12). Engage-snap spikes (max step ~0.9 @5-10 m/s, desiredAngle 394° @0-5 m/s) are engage/disengage artifacts, not steady wobble. **Need a drive sustaining 54–90 km/h to actually judge the kp bump.**
- Line 43 (`0.000071`) is the shared default for X50/S70 — leave it.

**Levers ranked:** (1) raise kf — DONE; (2) D-term — PID supports `k_d`/`error_rate` but `latcontrol_pid` doesn't pass them (wired but unused; code change to enable); (3) LAT_SMOOTH — weaker than thought (desired-angle already quiet, p95 step 0.2-0.3°). Do NOT reduce kp (→ understeer).

---

# Longitudinal — analysis (2026-08-09), NOT changed yet

**Two-stage architecture:**
1. **Planner** (`longitudinal_planner.py`, MPC) → `aTarget` (desired accel). `T_FOLLOW`, lead logic, `DANGER_VREL` act here.
2. **LongControl PI** (`longcontrol.py`) → gas/brake to track `aTarget`:
   `output = aTarget + kp×(aTarget − aEgo) + ki×integral` — `aTarget` IS the feedforward ("kf=1.0", hardcoded; **no tunable long kf**).

**Mental model:** planner = **WHEN** to slow (T_FOLLOW); kp/ki = **HOW smoothly** the car follows. Foot analogy: T_FOLLOW = brain deciding "lift now"; kp/ki = foot moving the pedal (how fast/firm). "Lift early AND smooth" needs **both** (bigger T_FOLLOW + slightly lower kp/ki).

**Drives analyzed:** 2026-08-08--13-44-09 (36 seg) + 12-19-52 (32 seg). Both engaged (`longActive` 67%, `carName=Proton X70 FL`, `latControlState=pidState`). qlogs (all 68) + 7 full-rate rlogs in `result/drives/`. Scripts: `result/pid_analyze2.py`, `scan_qlogs.py`, `long_override_scan.py`, `long_override_detail.py`, `lead_approach_analysis.py`. **Device SSH: `ssh kommu@192.168.0.9`** (default id_rsa; comma@/root@/lutfime@ all fail; sshd is publickey-only).

**Driver overrides (strict longActive, vEgo>7):** 13-44-09: 10 brake + 8 gas; 12-19-52: 8 brake + **30 gas**. NOT corners (curvature at overrides tiny, latacc 0.2-0.5 m/s²) — it's **lead-following**.
- Gas overrides: lead present 29/30, dRel ~20 m, TTC ~40 s, OP `aTarget` −0.2 to −0.3 (braking) → OP brakes for non-threatening leads, driver gasses to undo.
- Brake overrides: OP `aTarget` +0.11 (accelerating) with a lead → OP under-reacting to real leads.

**Late-brake ("full speed then brake") at ~60 km/h (vEgo 13-20, closing ~1.9 m/s), aTarget vs dRel:**
| dRel | 40-50 m | 30-35 m | 25-30 m | 20-25 m | 15-20 m | 10-15 m |
|---|---|---|---|---|---|---|
| aTarget med | ~0.00 | +0.01 | −0.26 | −0.37 | −0.70 | −0.94 |
OP holds FULL SPEED (aTarget ~0) until ~25 m, then ramps. Brake-start ≈ `T_FOLLOW × vEgo` = 1.25 × 17 ≈ 21 m. Closing 1.9 m/s < `DANGER_VREL` 4.5 → danger ramp didn't fire; only gap logic acts.

**Why it doesn't slow earlier:** planner holds set-speed until the gap is about to breach `T_FOLLOW` — reactive to the gap, not anticipatory (doesn't coast early for a seen slow lorry). It computes "can I keep speed and still meet the gap later? yes → full speed."

**Levers analyzed — decision: NONE changed yet:**
- **`T_FOLLOW`** (`long_mpc.py`: aggressive 1.25 s / standard 1.45 / relaxed 1.75): bigger gap → decel STARTS earlier AND smoothly (MPC-managed). 1.25→1.75 moves brake-start 21 m → ~30 m; →2.0 ≈ 34 m. **PRIMARY lever for "slow earlier".** Easiest: relaxed personality.
- **`DANGER_VREL_ON`** (planner: 4.5/3.5): WRONG tool — fixed −1.0 to −1.2 m/s² emergency decel (×`BrakeMagGain`; `BrakeMagGain=0` → multiplier `1.0` = normal, NOT off). Lowering threshold → brakes earlier but HARDER/abrupt (worsens aggressive feel). Don't use for smoothness.
- **`kp`/`ki`** (interface.py: kpV [0.7,0.5,0.4], kiV [0.2,0.15,0.1], `longitudinalActuatorDelay` 0.45): tracking smoothness/promptness, NOT when decel starts. Lower = smoother but lazier actual brake (risks "brake late"); higher = snappier but jerkier (part of the 4-6 m/s³ jerk). Complementary to T_FOLLOW.
- **Personality caveat:** `selfdrived.py:422-424` — if the car reports a stock gap (`CS.personality≠-1`) it OVERRIDES the OP param. Drives had mixed personality (aggressive/relaxed/standard) — likely the car's ACC gap button. Locking OP personality alone may not hold unless the car's gap is also set closest.
- MPC lead-following weights (true anticipatory coast) = compiled into the MPC → bigger change to tune.

**Next:** road test lateral `kf=0.0000250` first; then pick ONE long change per drive (relaxed personality → T_FOLLOW edit → kp/ki softening). Re-run `result/pid_analyze2.py` + `lead_approach_analysis.py` to compare.

---

# Actuator delays (2026-08-09, 7 full-rate rlogs; `result/delay_measure.py`)

- **`longitudinalActuatorDelay` = 0.45 ✓ FINAL** — measured median **494 ms** (p25 309, p75 767). Clean measurement (long controller is feedforward-dominant → this is real actuator delay). Matches config → keep.
- **`steerActuatorDelay` = 0.17 ✓ FINAL (don't change)** — raw median came out 568 ms but that's confounded by integral-dominance: at HIGH speed (f∝vEgo² meaningful) lag = **129–180 ms ≈ 0.17** (seg 13@24 m/s=129, seg 4@24.5=180). At LOW speed (f tiny, integrator carries it) apparent lag = 600–800 ms. So the big low-speed number is the kf/integral issue, not actuator delay. Confirmed correct by highway runs. Re-measure after kf raise — low-speed lag should drop.

---

# Longitudinal tunables — full catalog (all vars that affect long)

Reminder: **planner vars = WHEN/HOW-MUCH to slow; PI gains = HOW SMOOTHLY the car tracks; actuator scales = pedal sensitivity.** "runtime" = edit the file + restart (no rebuild). "build-time" = needs acados MPC regenerate+recompile.

**A. Following gap & lead response (planner/MPC, runtime)**
- `T_FOLLOW` (`long_mpc.py` get_T_FOLLOW): agg 1.25 / std 1.45 / relaxed 1.75 s. Bigger → follows further back + eases off earlier. **PRIMARY for "slow earlier".**
- `LongitudinalPersonality` (param): picks T_FOLLOW + jerk_factor. Caveat: car's stock ACC gap (`CS.personality`) overrides it (`selfdrived.py:422`).
- `LEAD_DANGER_FACTOR` (`long_mpc.py` 0.75): MPC safety margin to lead. Higher → more standoffish.
- `STOP_DISTANCE` (`long_mpc.py` 7.0 m): target stopped gap behind lead.

**B. Closing-rate emergency ramp (planner, runtime) — NOT for smoothness (firm decel)**
- `DANGER_VREL_ON/OFF_MPS` (4.5/3.5): closing threshold to trigger forced decel. Lower → earlier but FIRM.
- `DANGER_DECEL_VEGO_V/BP` ([-1.0,-1.2] @ [0,10]): decel magnitude commanded.
- `DANGER_DECEL_RAMP_RATE` (1.0 m/s³): ramp-in speed.
- `DANGER_HOLD_SECONDS` (0.2): hysteresis hold.
- `BrakeMagGain` (param, =0 → mult `1 + pct/100` = 1.0): scales danger decel + ramp. Raise → harder braking.

**C. Accel/decel limits (planner + interface, runtime)**
- `A_CRUISE_MAX_VALS/BP` (planner [1.6,1.2,0.8,0.6] @ [0,10,25,40]): max positive accel by speed. Lower → gentler (fixes "aggressive").
- `CRUISE_MAX/MIN_ACCEL` (`long_mpc.py` 1.6 / -1.2): MPC accel bounds.
- `_A_TOTAL_MAX_V/BP` (planner [1.7,3.2] @ [20,40]): combined lat+long accel cap (limits accel in corners).
- `COMFORT_BRAKE` (`long_mpc.py` 2.5): comfort decel threshold.
- `stopAccel` (-0.8), `stoppingDecelRate` (0.3), `startAccel` (1.2), `startingState` (True): stop/start behavior.

**D. PI tracking gains (interface, runtime) — smoothness, NOT timing**
- `longitudinalTuning.kpV/kpBP` ([0.7,0.5,0.4] @ [0,5,20]): proportional tracking gain. Lower → smoother but lazier.
- `longitudinalTuning.kiV/kiBP` ([0.2,0.15,0.1]): integral. Lower → less overshoot, smoother.
- `longitudinalActuatorDelay` (0.45 ✓): delay compensation (measured correct).
- `ALLOW_THROTTLE_THRESHOLD` (0.4) / `MIN_ALLOW_THROTTLE_SPEED` (2.5): when OP throttles vs coasts/brakes.

**E. Actuator scaling (carcontroller, runtime) — pedal sensitivity**
- `ACCEL_POSITIVE_SCALE` (16): gas accel→CAN. Higher → gas touchier.
- `ACCEL_NEGATIVE_SCALE` (23): brake accel→CAN. Higher → brake touchier.

**F. MPC deep weights (`long_mpc.py` — RUNTIME, edit + restart planner, NO rebuild): true smoothness/anticipation**
- Confirmed MPC runs long in normal mode: planner `mode='acc'` → `aTarget = output_a_target_mpc` (radar leads). The 0.10/0.11 model is lateral-only in acc mode; `modelV2.action.desiredAcceleration` only used in blended/experimental, and only as `min(mpc, e2e)`.
- `J_EGO_COST` (5.0): jerk cost weight. Higher → smoother (less jerk).
- `A_CHANGE_COST` (200.0): penalizes accel changes. Higher → smoother but slower response.
- `X_EGO_OBSTACLE_COST` (3.0), `DANGER_ZONE_COST` (100): obstacle/danger costs.
- Applied at runtime via `self.solver.cost_set('W', W)` each plan (built from these Python constants × `jerk_factor`) → **edit + restart planner, NO acados rebuild**. In acc mode `W = diag(3, 0, 0, 0, jerk_factor×A_CHANGE_COST, jerk_factor×J_EGO_COST)`. `relaxed` personality already raises both via `jerk_factor=1.0` (vs aggressive 0.5) → smoother long.

**Suggested experiment order (ONE change per drive):**
1. relaxed personality (T_FOLLOW 1.75) — easiest; fixes "late brake" + smoother.
2. If still aggressive: lower `A_CRUISE_MAX` + small `kp`/`ki` softening.
3. If lead over-braking persists: nudge `LEAD_DANGER_FACTOR` / `DANGER_VREL` (careful — firm).
4. True anticipation/smoothness: MPC weights `J_EGO_COST`/`A_CHANGE_COST` — **runtime edit, no rebuild** (try after relaxed personality).

---

# Experimental/CEM — toggle-respecting (2026-08-09)

`conditional_experimental_mode.py` is Kommu-only. Upstream comma: experimental = pure UI toggle (`selfdrived.py:566` reads `ExperimentalMode` param). Kommu's CEM *overwrote* that param based on personality/curvature/slow-lead/model-stopping — and **aggressive personality gated it OFF**, which blocked corner-slowing.

**Change (committed via merge c0a56a4, present on x70-map):** the final `self.params.put_bool("ExperimentalMode", should_enable)` override is **commented out** (lines 121-124), and `self.cem_enabled` is forced `False` (line 30). Net effect: **experimental mode follows the UI toggle only** (comma-style). CEM's detection logic still computes but no longer writes the param. `self.cem` wiring in controlsd.py is untouched (per user: minimal edit, don't remove the var).

---

# Map-Corner Slowdown (x70-map, NEW — handoff from other agent)

**Branch:** `x70-map`, commit `e2dd6ba`. Ported from KisaPilot/sunnypilot mapd (pfeiferj, MIT). **INSTALLED on device 2026-08-09** (staging-x70fix overlay): files deployed, deps installed, params_pyx.so rebuilt, `mapd_kommu` registered + manager boots clean, feature OFF by default. **On-road test (Test 2/5) still pending** (device was offroad/home wifi during install).

**What it does:** new process `mapd_kommu` reads GPS (1 Hz), fetches OSM road geometry (Overpass, multi-endpoint fallback), matches position+heading to a road, computes upcoming curvature via spline → advisory corner speed `v = sqrt(budget/kappa)`. The longitudinal planner clamps `v_cruise = min(v_cruise, v_map)` before MPC → MPC plans a smooth decel. **Slowdown-only by `min()` construction** (can never accelerate). If GPS/OSM/match fails → `MapCornerValid=false` → planner ignores (identical to stock). Master enable **default off**.

**Files (device side):** `selfdrive/mapd_kommu/` (10 files); `longitudinal_planner.py:~144` (the `min()` clamp, 16 lines); `system/manager/process_config.py:101-103` (KA2-only on-road process); `common/params_keys.h:47-51` (5 keys); `pyproject.toml` (+overpy, +scipy).
**Tuning params:** `MapCornerEnabled` (off), `MapCornerBudget` (2.5 m/s²; lower=slower), `MapCornerLookahead` (200 m). Published: `vCruiseMapCorner`, `MapCornerValid`.

**⚠️ DEVICE CONSTRAINT — param keys (RESOLVED 2026-08-09, see [[params-pyx-rebuild-constraint]]):** new param keys need `params_keys.h` + `params_pyx.so` rebuilt. The device **CAN** rebuild (scons+Cython+gcc present — earlier "can't rebuild" note was wrong). Verified procedure: backup `.so` → `rm prebuilt` → stub `panda/certs/{debug,release}.pub` (1024-bit RSA; fork lacks them, SConstruct parse fails) → `scons -j4 common/params_pyx.so` → verify round-trip → **`touch prebuilt`** (MUST restore or manager's boot `build.py` loops on scons failure → openpilot won't boot) → restart. Rollback = `cp params_pyx.so.stock back`.

**BUGS found + fixed during install (commit a1413eb):** the device's params layer enforces STRICT typing (`PYTHON_2_CPP` cast table) — `(str,FLOAT)` and `(str,BOOL)` are absent → `TypeError`.
- `mapd.py` wrote FLOAT `vCruiseMapCorner` as a format string → would crash mapd on publish. Fixed: `float(v_corner)`.
- iOS `DeviceService.saveMapCornerParam` wrote all keys via `put(k, v)` str → BOOL/FLOAT writes fail silently. Fixed: dispatch by key (`put_bool` for enable, `float()` for budget/lookahead). **App needs rebuild+reinstall** for the slider to persist.
- Deps: scipy drags a numpy upgrade (pyproject says `>=2.0` but device pinned to 1.26.4). Install `scipy==1.13.1 --no-deps` (numpy-1.x ABI, coexists with 1.26.4). venv root-owned → sudo; `/home` is 100M tmpfs → uv cache+TMPDIR on `/data`.

**Install steps (handoff):** deploy files to `/data/openpilot` + mirror the 3 edited files to `/data/safe_staging/merged/` (overlay twin — see [[device-deploy-overlay-gotcha]]); `pip install overpy scipy` into `/usr/local/venv`; rebuild/bypass params keys; restart openpilot (`sudo systemctl restart kommu.service`); verify `mapd_kommu` in `ps aux` when on-road.

**Test plan:** (1) offline math `tools-local/test_map_corner.py`; (2) process starts on-road; (3) params round-trip (the gate); (4) dry-run drive (feature off, log `vCruiseMapCorner`); (5) live test on known corner (LOW traffic). Keep default off; disable immediately if odd braking.

---

# Curve Speed Controller (CSC, FrogPilot port) — now the DEFAULT corner-slowdown (2026-08-09)

**CSC = model-curvature based** (`modelV2.orientationRate.z / velocity.x` → `v = sqrt(budget/curvature)`), clamps `v_cruise` via `min()`. 4 profiles (MapCornerProfile): Gentle=1.5, **Standard=2.0 (default)**, Sport=self-tuning (MapCornerMaxLimit), Auto=learns (CurvatureData/CalibrationProgress). Pure Python (numpy). Look-ahead via the model trajectory (`time_to_point`).

**Decoupled from mapd (the bug + fix):** CSC was originally gated by `MapCornerEnabled` (shared with mapd), so disabling mapd also killed CSC. Fixed: CSC now gated by its own `CSCEnabled` (default ON, BOOL key added → params_pyx.so rebuilt, 10 keys total). mapd stays opt-in via `MapCornerEnabled` (default off). **Default state: CSC on (Standard), mapd off.** iOS "Curve Slowdown" toggle rewired to CSCEnabled (was MapCornerEnabled).

**CSC vs mapd replay (10-30-57 drive, `result/` replay scripts):**
| Corner | Real | CSC (model) | mapd (OSM) |
|---|---|---|---|
| C4 3.10022,101.70833 (normal) | ~30 km/h (per driver) | **34 km/h** ✓ | 14 km/h ✗ (phantom — no sharp OSM node within 60m; spline artifact) |
| C1 3.09976,101.70475 (sharp, U-turn ahead) | ~14 | **15 km/h** ✓ | 14 km/h ✓ (agrees) |

- **CSC is more accurate** on normal corners (C4: 34 vs phantom 14) and matches mapd on real sharp ones (C1). Earlier model-latacc analysis missed C4 (latacc=v²×r too small at low speed) — CSC uses curvature (yawrate/speed), which catches it.
- **CSC looks ahead**: first flags C4 at ~16s/56m out (advisory ramps 52→34 km/h). Resolves the "does it show forward data" concern — yes, via the model trajectory (~10s horizon).
- Advisory is somewhat noisy as the model updates (C4 bounced 68→34→50); fine in practice — planner takes `min()`, MPC smooths the decel.
- **Verdict:** model-based CSC is the reliable primary; OSM-based mapd is unreliable (phantom corners from junction/spline artifacts) → disabled by default. On-road validation still pending (offroad during install).

---

# "Active → standby (blue) mid-drive" incident investigation (2026-08-10)

**Symptom (user):** on a 2026-08-09 highway drive (>50 km/h, likely `04-01-58` which has 59–87 km/h cruise-disables), OP went active→standby with no felt intervention; couldn't re-engage for a few minutes; **Kommu showed no error, but the Proton HUD said "cannot enable cruise"**. Dangerous.

**NOT our code (verified):** no driving-critical process crashed — controlsd/modeld/planner clean across all logs (only a thermald SIM-modem `IndexError`, hardware-monitoring, can't disengage). No `steerFault`/`accFault`/`canError`/`controlsFailed`/`thermal`/`commIssue` events anywhere. Event vocabulary was only driver-input types (`steerOverride`, `brakeHold`, `gasPressedOverride`, `steerSaturated`).

**Proton brake signal layout (DBC `proton_general_pt.dbc`):**
- `brakePressed` ← `PARKING_BRAKE` msg 293, **bit 1** (`BRAKE_PRESSED`). Distinct bit from `CAR_ON_HOLD` (bit 27) and `ESC_ON` (bit 16). NOT a same-bit mis-map.
- brake pressure ← `BRAKE` msg 291, `BRAKE_PRESSURE` (16-bit, scale 0.0002).

**brakePressed is RELIABLE (not glitchy — corrected an earlier wrong hypothesis):** on `04-01-58`, `brakePressed=True` 10.1% of frames; when True, **86% had real pressure (>0.05)**, only 2% near-zero, only **6% overlapped auto-hold**. So it tracks the pedal, not hold/noise. The earlier "min=0.00 → spurious" read was misleading (that's just 2% of frames).

**Conclusion:** the disengages were **real light braking** (brake pressure present, ~10% of the highway drive = traffic braking; user may not have registered each light press). The **"cannot enable cruise for minutes" is car-side** — a Proton cruise-availability cooldown/fault after the brake/disengage (HUD confirms; OP can't engage when the car refuses cruise). Same class as "heavy rain disables cruise".

**Takeaways:**
- If "cannot enable cruise for minutes" recurs → it's a **Proton-side cruise reliability quirk**, not OP/CSC/mapd. Nothing OP can do (OP can't override the car refusing cruise). Worth a Proton service flag if frequent.
- Don't waste effort "fixing" brakePressed — it's reliable. (Considered deriving it from pressure; unnecessary.)
- The dangerous-feeling part (no OP for minutes) = the car withholding cruise, not a Kommu fault.

**Why Kommu showed NO "cruise unavailable" (tracking gap):** `carstate.py:183` hardcodes `ret.cruiseState.available = True`, so OP never sees the Proton's actual cruise-available state — the "cannot enable cruise" only ever showed on the car's HUD. The Proton DOES broadcast it on CAN — `CRUISE_AVAILABLE` (`PCM_BUTTONS` msg 419, bit 17) and `CRUISE_DISABLED` (`ACC_CMD` msg 417, bit 12) — so it's trackable. TODO fix: `ret.cruiseState.available = bool(cp_cam.vl["PCM_BUTTONS"]["CRUISE_AVAILABLE"])` instead of the hardcoded True, so OP logs/surfaces the "cannot enable cruise" state instead of silently failing to engage. (Can't retroactively verify past incidents — OP logged the forced-True carState, not the raw bit.)

---

# CSC real-drive validation (2026-08-10 drives) + device-clock gotcha

**⚠️ DEVICE CLOCK IS BROKEN:** timezone misconfigured (device `date` showed "PST"). `logMonoTime` + route names do NOT map to MYT (all 2026-08-10 drives logMonoTime→MYT ~08:00, but file-mtime shows the real afternoon times). **Use file mtime (`ls -lt`) for real wall-clock order/times, NOT logMonoTime or route names.** TODO: fix the device timezone.

**CSC IS WORKING** (corrected an earlier wrong "model blind / curvature=0" claim — that was noise frames). On the 2026-08-10 afternoon drive (`05-58-42`, ended ~2:37pm per file-mtime):
- **The model DOES detect corners** — both `desiredCurvature` (steering) AND `orientationRate.z/velocity.x` (CSC) read ~0.011–0.012 in turns. The signal **flickers** (0 ↔ 0.012 frame-to-frame), not consistently blind.
- **CSC triggers correctly when advisory < speed:**
  - ~2:20pm corner (seg21, real steer −54°): CSC advisory dropped **78→43 km/h** (detected), car decelerated **79→62 km/h** (aTarget −1.9 m/s²). CSC acted. **BUT a lead was present** → the decel was a mix of CSC + lead-following, indistinguishable to the driver.
  - ~2:25pm corner (seg25, steer 34° @ 44km/h): CSC advisory **51 km/h** > speed 44 → CSC correctly **idle** (already safe). The 45→19 decel was **lead-following** (lead present 100%).
- **Conclusion:** CSC works (detects via model curvature, triggers on speed-vs-advisory). On these drives its effect was **masked by lead cars** at every sharp corner. To *feel* CSC, need a **fast (60–80 km/h) corner with NO lead**.
- **mapd skip-when-CSC-on:** added (planner skips mapd clamp when `CSCEnabled`) so mapd's OSM phantom advisories can't override CSC via `min()`. mapd forced OFF (`MapCornerEnabled=False`) — re-enabled earlier only because the OLD (un-rebuilt) iOS app toggles `MapCornerEnabled`. Fix = rebuild app (toggle → CSCEnabled).

**CSC analysis method (no dedicated log field):** recompute advisory from `modelV2` (`advisory = sqrt(2.0 / max|orientationRate.z/velocity.x|)`, Standard budget), check `longitudinalPlan.aTarget` for the decel + `radarState.leadOne.status` for lead masking. Scripts: `result/csc_activity.py`, `result/corner_concrete.py`, `result/seg21_decel.py`, `result/curv_signal_compare.py`. Optional TODO: add a direct CSC-advisory log field (capnp change) for byte-exact capture.

**ki decrease (lateral) — VALIDATED 2026-08-10 (CORRECTED: earlier "didn't help" was wrong — invalid comparison, both drives had same ki):**
- **Earlier analysis was INVALID:** morning + afternoon drives both used the ORIGINAL ki (CarParams in rlog confirmed `[0.001,0.01,0.09,0.4,0.5]`). The decreased ki was in interface.py but NOT active (no restart). My "i/OUT 43% unchanged → didn't help" was meaningless.
- **VALID test (drive `11-04-40`, ki verified active `[0.001,0.005,0.05,0.4,0.5]` via CarParams):**
  - **i/OUT: 55% → 26%** (halved — integrator role much smaller). ✓
  - **Overshoot MAGNITUDE: median 7.4° → 3.9°** (nearly halved — each overshoot event milder, much less felt). ✓ User confirms: "doesn't really overshoot anymore."
  - **Entry error median: 0.0° both drives** (no entry-lag penalty — earlier "9.5° entry worse" was a misleading MEAN skewed by outliers). ✓
  - **Verdict: KEEP the decreased ki.** It halved overshoot severity with no entry penalty. Committed in git (`[0.001,0.005,0.05,0.4,0.5]`).
- **Lesson:** always verify via CarParams in the rlog that the tuning was actually active before comparing. And use MEDIAN (not mean) for angleError — it's robust to outliers.

**CSC validation on real drive (`11-04-40`, decreased ki):** user reports "working quite ok." Data: 2.4% would-slow (200 frames), min advisory 14 km/h (detected sharp corners), advisory median 60 km/h vs speed median 29 km/h (city — CSC mostly idle, correctly). CSC is detecting + advising at sharp corners. Effect sometimes masked by leads (earlier finding).


