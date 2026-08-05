# Corner-Speed Cap — Design Guide (no code changes yet)

> Goal: make the X70 scrub speed in tight corners, **without touching the model** and
> **without affecting any braking that isn't corner-related** (stops, traffic, lead cars).
>
> Status: **DESIGN ONLY.** Nothing is applied. Review this, then implement when ready.

---

## Why this is needed

The 0.11 end-to-end model decides corner speed via `modelV2.action.desiredAcceleration`.
In blended/Experimental mode (which this build keeps always-on — see
`conditional_experimental_mode.py:30`, `cem_enabled = False`), the model's braking decision
wins at `longitudinal_planner.py:209`:

```python
output_a_target = min(output_a_target_mpc, output_a_target_e2e)   # E2E dominates
```

So the model's judgment (~50 km/h through a given radius) is what the car does. To make
corners slower **without retraining the model**, we add a corner-conditional cap that only
adds deceleration when the car is *actually cornering beyond a lateral-accel budget*.

---

## Scope: X70 ONLY

This must be gated so it only runs on the X70. The math (wheelbase, steerRatio) is
X70-specific, and we don't want to change behavior for X50/X90 or other platforms.

X70 constants (from `opendbc_repo/opendbc/car/proton/values.py:85`):
- `wheelbase = 2.67 m`
- `steerRatio = 15.0`

Gate with:
```python
if self.CP.carFingerprint == CAR.PROTON_X70:
    ...
```

---

## The math (X70-tuned)

Lateral acceleration from steering angle + speed (bicycle model):

```
a_y = v^2 * |steeringAngleDeg| * (pi/180) / (steerRatio * wheelbase)
    = v^2 * |steeringAngleDeg| * 0.00393 / 40.05        # X70 precomputed
```

Worked examples (X70):
| Speed | Steering angle | a_y (m/s²) | Feel |
|------:|---------------:|-----------:|------|
| 50 km/h (13.9 m/s) | 60°  | 1.14 | gentle |
| 50 km/h (13.9 m/s) | 120° | 2.28 | firm |
| 70 km/h (19.4 m/s) | 90°  | 3.30 | hard corner |
| 30 km/h (8.3 m/s)  | 150° | 0.97 | tight low-speed (ignored — below 8 m/s floor) |

---

## Exact insertion point

**File:** `selfdrive/controls/lib/longitudinal_planner.py`
**After:** line 210 (after `output_a_target` is set, before the lead/danger logic at 212)

```python
# ---- existing code at 205-210 (unchanged) ----
if mode == 'acc':
    output_a_target = output_a_target_mpc
    self.output_should_stop = output_should_stop_mpc
else:
    output_a_target = min(output_a_target_mpc, output_a_target_e2e)
    self.output_should_stop = output_should_stop_e2e or output_should_stop_mpc

# ============ INSERT CORNER CAP HERE ============
# X70 corner-speed cap: force decel when lateral accel exceeds budget.
# ONLY active on PROTON_X70; all other platforms untouched.
import math as _math
CORNER_LAT_ACCEL_BUDGET = 2.5   # m/s^2 — lower = slower corners (2.5 = mild, 2.0 = conservative, 1.5 = very cautious)
CORNER_MIN_SPEED = 8.0          # m/s — don't activate below ~29 km/h (parking/low-speed maneuvers)
if (self.CP.carFingerprint == CAR.PROTON_X70
        and mode != 'acc'
        and sm['carState'].vEgo > CORNER_MIN_SPEED):
    _v = sm['carState'].vEgo
    _ang = abs(sm['carState'].steeringAngleDeg)
    _ay = _v * _v * _ang * _math.pi / 180.0 / (self.CP.steerRatio * self.CP.wheelbase)
    if _ay > CORNER_LAT_ACCEL_BUDGET:
        # Proportional decel: bigger overshoot = harder brake. Gentle, not jerky.
        _overshoot = (_ay / CORNER_LAT_ACCEL_BUDGET) - 1.0
        _corner_decel = -1.5 * _overshoot          # m/s^2
        output_a_target = min(output_a_target, _corner_decel)
# ============ END CORNER CAP ============

# ---- existing code at 212 (unchanged) ----
lead = sm['radarState'].leadOne
```

---

## What this does NOT touch

- ❌ The model (`desiredAcceleration` is read, never modified)
- ❌ Stop / traffic / lead-car braking (those come from `shouldStop` + MPC, untouched)
- ❌ Throttle / acceleration (only *adds* decel, never removes it; only in corners)
- ❌ Other car platforms (gated to `PROTON_X70`)
- ❌ Low-speed maneuvers (floor at 8 m/s ≈ 29 km/h)
- ❌ ACC mode (only runs in blended/experimental)

---

## Tuning

Change one number: `CORNER_LAT_ACCEL_BUDGET`.

| Value | Behavior |
|------:|---------|
| 3.0 | stock-ish — rarely triggers |
| **2.5** | **recommended start — mild scrub on aggressive corners** |
| 2.0 | conservative — clearly slows for most corners |
| 1.5 | very cautious — firm braking for any meaningful curve (mountain roads) |

Drive the same corner at 2.5, then 2.0, until it feels right. Reversible — set to 3.0
(or delete the block) to fully disable.

### Optional: make it live-tunable via Params
Wrap the budget in a Params read so you can change it from the device without restarting:

```python
# read once per update (cheap); default 2.5 if unset
_budget_raw = self.params.get("CornerLatAccelBudget")
CORNER_LAT_ACCEL_BUDGET = float(_budget_raw) if _budget_raw else 2.5
```

Then `params put CornerLatAccelBudget "2.0"` on the device changes it live.

---

## Implementation checklist (when ready)

- [ ] Confirm the device repo (`/data/openpilot`) is on branch `staging-x70fix`
- [ ] Apply the insert at `selfdrive/controls/lib/longitudinal_planner.py:210`
- [ ] Add `from opendbc.car.proton.values import CAR` import if not present
- [ ] Restart controlsd (or reboot device): `pkill -f controlsd` or `tmux a` → restart
- [ ] Test-drive the 2pm corner, verify speed scrubs as expected
- [ ] Adjust `CORNER_LAT_ACCEL_BUDGET` to taste

---

## Alternative considered (rejected): global brake gain

Multiplying `output_a_target_e2e` by 1.2 would make **every** brake event 20% harder —
stops, traffic, lead cars, corners alike. Rejected because the goal is corner-specific.
The corner cap above only activates when there's an actual corner.
