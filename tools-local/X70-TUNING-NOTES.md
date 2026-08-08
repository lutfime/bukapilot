# X70 PID Lateral Tuning Notes

## Current State (2026-08-08)
- **Controller:** PID (default; torque opt-in via toggle)
- **LAT_SMOOTH_SECONDS:** 0.15 (0.10 model path) / 0.0 (0.11 path)
- **kpV:** [0.0005, 0.02, 0.045, 0.10, 0.17] at [0, 5, 15, 25, 35] m/s
- **kiV:** [0.001, 0.01, 0.09, 0.4, 0.5]
- **kf:** 0.000006
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
