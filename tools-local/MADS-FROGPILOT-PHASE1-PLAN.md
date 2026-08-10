# MADS Phase 1 — FrogPilot-style Always-On Lateral (Python-only)

**Status:** IMPLEMENTED + staff-reviewed (2 critical/major bugs fixed) — ready for on-road test
**Created:** 2026-08-09
**Last updated:** 2026-08-10 (post-review fixes)
**Branch base:** `x70-test` / `x70-map` (openpilot 0.10.3-based bukapilot, KA2)
**Supersedes:** `tools-local/MADS-IMPLEMENTATION-PLAN-ABANDONED.md` (deprecated 2026-08-09)

---

## What was built (actual implementation)

### Design principle: zero behavior change when MADS is off or cruise is enabled

All MADS code is gated behind `self.lat_only`, which is True ONLY when:
MADS toggle ON AND MAIN armed (`cruiseState.available`) AND stock ACC off
(`not cruiseState.enabled`) AND in Drive. When `lat_only=False`, the 2 modified
original lines evaluate identically to the original code (`and not False` = no-op).

### The 3-state model (FrogPilot standby)
- **Disabled**: MAIN off → nothing
- **Standby**: MAIN armed, no SET → **lateral on, gas manual**
- **Enabled**: SET pressed → full lat + long (normal ACC)

### Files changed (4 files — minimal, no firmware, no carstate.py)
| File | Original lines modified | Additions | When it runs |
|---|---|---|---|
| `appbridged.py` | 0 | +24 (toggle plumbing) | Always (no behavior change) |
| `selfdrived.py` | **2** (gas gate + mismatch counter — each gets `and not self.lat_only`) | +40 (init, lat_only compute, event strip block) | Only when `lat_only=True` |
| `controlsd.py` | 0 | +6 (pure append after line 109) | Only when `mads_enabled and latActive and not cruiseState.enabled` |
| iOS Swift (2 files) | 0 | +12 (toggle UI) | Always (UI plumbing) |

**NOT touched:** `carstate.py`, `carcontroller.py`, `proton.h`, `main.c`, `params_keys.h`, any firmware.

### The 2 modified lines in selfdrived.py (the only changes to existing code)
```python
# Line 1 — gas gate: gas doesn't disengage in standby. Brake always does.
gas_disengage = CS.gasPressed and not self.CS_prev.gasPressed and self.disengage_on_accelerator
if (gas_disengage and not self.lat_only) or \    # ← added "and not self.lat_only"

# Line 2 — mismatch counter: skip in standby (controls_allowed is false but
# Proton tx_hook doesn't gate steering on it)
if self.enabled and not self.lat_only and any(not ps.controlsAllowed ...  # ← added "and not self.lat_only"
```
When `lat_only=False`: both lines are identical to original. Zero behavior change.

### How engagement works
1. MAIN armed → `cruiseState.available = True` → our `lat_only = True`
2. We inject `pcmEnable` → state machine: `disabled` → `enabled`/`active`
3. `controlsd.py:107`: `CC.latActive = selfdriveState.active` → steering on
4. We strip `pcmDisable`/`buttonCancel`/`cruiseDisabled` (NOT pedalPressed — brake always disengages)
5. `mismatch_counter` increment skipped when `lat_only` (avoids controlsMismatch from controls_allowed=false)
6. controlsd: `CC.longActive = False` in standby → ACC released to driver
7. User presses SET → `cruiseState.enabled = True` → `lat_only = False` → normal ACC
8. User presses brake → `pedalPressed` fires → `USER_DISABLE` → disengages (ALWAYS)

### Staff review findings (2026-08-10) — all fixed
| Issue | Severity | Fix |
|---|---|---|
| pedalPressed stripped → brake won't disengage | **CRITICAL** | Removed from strip list. Gas handled by gate; brake flows through → USER_DISABLE |
| lkaDisabled proxy unreliable (stock LKA could trigger MADS) | **MAJOR** | Now uses `cruiseState.available` (same signal selfdrived already trusts at line 603) |
| lat_only computed after pedal block (stale by 1 frame) | **MINOR** | Moved above pedal block |
| controlsMismatch strip too broad (could hide safety-mode mismatches) | **MAJOR** | Removed from strip list. Now gated at source (mismatch_counter skip). Safety-model/rxCheck paths still fire |

### Safety integrity (verified by review)
- **Brake ALWAYS disengages** — pedalPressed NOT stripped ✓
- **Gas does NOT disengage in standby** — gated by `not lat_only` ✓
- **controlsMismatch from safety-model/rxCheck still fires** — only controls_allowed path skipped ✓
- **All IMMEDIATE_DISABLE events remain active** ✓
- **Zero behavior change when MADS off or cruise enabled** ✓

---



## Goal

Standby lateral steering for the Proton X70: openpilot steers while cruise is in
standby (MAIN armed, no SET), gas pedal is manual, brake disengages. Same UX as
the old working KA1 device, but on KA2 — **without** rebuilding panda firmware.

## Why this approach (not the old plan, not sunnypilot)

Research findings that drove this design (see Background at bottom):

1. **Proton's `tx_hook` does NOT gate steering on `controls_allowed`.** Steering
   frames pass as long as LKAS bits are consistent + torque ≤ cap. The two firmware
   guards (`pcm_cruise_check` + the heartbeat-engaged counter in main.c:202) only
   feed the `controlsMismatch` check in selfdrived.py — they do NOT block torque.
2. **FrogPilot's Always-On Lateral works by overriding `CC.latActive` in controlsd,
   not by faking cruise state.** Much smaller blast radius than the old `lat_only`
   carstate latch (which both production forks avoid).
3. **`ALWAYS_ON_LATERAL` stock flag does NOT exist in this 0.10.3 firmware.**
   `opendbc_repo/opendbc/safety/declarations.h` only defines `ALT_EXP_DISABLE_STOCK_AEB`,
   `ALT_EXP_RAISE_LONGITUDINAL_LIMITS_TO_ISO_MAX`, `ALT_EXP_ALLOW_AEB`. So FrogPilot's
   exact flag-flip won't work — but its *mechanism* (force `CC.latActive`) still does,
   because Proton's tx_hook doesn't care about `controls_allowed` for steering anyway.

## The hypothesis this plan tests

> If we keep openpilot's state machine in an `enabled`+`active` state during cruise
> standby (via events manipulation), `controlsAllowed` will stay true on the panda
> (because openpilot's heartbeat will report `engaged=true`), and BOTH firmware guards
> are satisfied — **no firmware rebuild needed.**

This is the opposite bet from the old plan. The old plan tried to bypass firmware
guards while openpilot stayed disengaged. This plan keeps openpilot engaged so the
guards never fire in the first place. The empirical test in Phase 1.4 confirms which
bet is right.

---

## Architecture (0.10.3 KA2 — two daemons)

```
selfdrived.py        → computes enabled/active from events, publishes selfdriveState
    │                  (runs the state machine in state.py)
    ▼
controlsd.py:100     → CC.latActive = self.sm['selfdriveState'].active and ...
    │                  (this is the FrogPilot injection point)
    ▼
carcontroller.py:75  → steer_enabled = CC.latActive  (LKAS torque gates on this)
carcontroller.py:178 → create_acc_cmd(..., CC.longActive, ...)  (ACC gates on this)
```

**Key:** `selfdrived` decides engage state; `controlsd` translates it to `CC.latActive`;
`carcontroller` consumes `CC.latActive` for steering and `CC.longActive` for ACC. We
inject MADS at the `selfdrived` event layer (so the heartbeat reports engaged) and add
a longitudinal carve-out in controlsd (so ACC stays manual in standby).

---

## Phase 1.1 — Toggle infrastructure (file-based, no Params rebuild)

**File: `selfdrive/appbridged/appbridged.py`** — copy the existing
`X70UsePidController` / `UseSupercomboModel` pattern (lines 129-148, 426, 458-459):

```python
MADS_TOGGLE_FILE = "/data/params/MadsEnabled"

def get_mads_toggle() -> bool:
    try:
        return open(MADS_TOGGLE_FILE).read().strip() == "1"
    except (FileNotFoundError, OSError):
        return False  # DEFAULT OFF

def set_mads_toggle(val: bool):
    try:
        if val:
            with open(MADS_TOGGLE_FILE, "w") as f:
                f.write("1")
        else:
            os.remove(MADS_TOGGLE_FILE)
    except (FileNotFoundError, OSError):
        pass
```
- Add to `send_settings_message`: `sett['MadsEnabled'] = get_mads_toggle()`
- Add to `saveToggle` handler: `if 'MadsEnabled' in settings: set_mads_toggle(bool(settings.pop('MadsEnabled')))`

**Why file-based:** no `params_pyx.so` rebuild, matches existing X70 toggles, survives
`clearAll` (lives in `/data/params/` not `/data/params/d/`).

**iOS:** add a "MADS (beta)" toggle row in `SettingsSheet.swift`, same pattern as the
existing "0.11 Model (beta)" toggle. Default OFF. Wire via `DeviceSettings.madsEnabled`.

---

## Phase 1.2 — selfdrived.py: keep engaged in cruise standby

**File: `selfdrive/selfdrived/selfdrived.py`** — this is the core MADS logic.

Read the toggle once at init (file-based, no Params):
```python
self.mads_enabled = False
try:
    with open("/data/params/MadsEnabled") as f:
        self.mads_enabled = f.read().strip() == "1"
except (FileNotFoundError, OSError):
    pass
```

### 1.2a — Track a `lat_only` flag (the standby state)
Near where cruise state is processed (after CS is built, ~line 380 where
`cruise_mismatch` is computed), add:
```python
# MADS: detect falling edge of stock ACC → enter lat_only (standby lateral)
if self.mads_enabled:
    stock_enabled_now = CS.cruiseState.enabled
    if stock_enabled_now or not CS.cruiseState.available or CS.gearShifter != car.CarState.GearShifter.drive:
        self.lat_only = False
    elif self.prev_stock_enabled:   # user just cancelled ACC → falling edge
        self.lat_only = True
    self.prev_stock_enabled = stock_enabled_now
else:
    self.lat_only = False
```
Init `self.lat_only = False` and `self.prev_stock_enabled = False` in `__init__`.

### 1.2b — Don't disengage on gas when in lat_only
The pedal-disengage block (~line 213-216):
```python
# Current:
if (CS.gasPressed and not self.CS_prev.gasPressed and self.disengage_on_accelerator) or \
  (CS.brakePressed and ...) or (CS.regenBraking and ...):
    self.events.add(EventName.pedalPressed)

# Change to (gas gate only — brake ALWAYS disengages, user's explicit choice):
gas_disengage = CS.gasPressed and not self.CS_prev.gasPressed and self.disengage_on_accelerator
if (gas_disengage and not self.lat_only) or \
  (CS.brakePressed and (not self.CS_prev.brakePressed or not CS.standstill)) or \
  (CS.regenBraking and (not self.CS_prev.regenBraking or not CS.standstill)):
    self.events.add(EventName.pedalPressed)
```

### 1.2c — Strip the events that would kill engagement in standby
When `lat_only` is active, several events fire that would disengage openpilot. We need
to remove them so the state machine stays `enabled`+`active`. Near the events update
(after `self.events = self.car_events.update(...)` ~line 203), add:
```python
if self.lat_only:
    # These fire when stock ACC cancels — keep openpilot engaged for lateral
    for ev in (EventName.pcmDisable, EventName.buttonCancel,
               EventName.cruiseDisabled, EventName.pedalPressed):
        if ev in self.events:
            self.events.remove(ev)
```
**Why this works:** with these events gone, `state_machine.update(events)` sees no
USER_DISABLE / IMMEDIATE_DISABLE, so `self.enabled` and `self.active` stay True. The
panda heartbeat then reports `engaged=true`, and BOTH firmware guards
(`pcm_cruise_check` sees openpilot engaged via the latch; the main.c:202 counter sees
`heartbeat_engaged=true`) are satisfied. **No firmware rebuild needed — if the
hypothesis holds.**

**Note on `pcmEnable`:** when the user presses SET to engage ACC from standby,
`pcmEnable` fires normally and openpilot resumes full lat+long. No special handling
needed — `lat_only` clears on the rising edge of stock ACC (1.2a).

---

## Phase 1.3 — controlsd.py: suppress longitudinal in standby

**File: `selfdrive/controls/controlsd.py:100-102`** — currently:
```python
CC.latActive = self.sm['selfdriveState'].active and not CS.steerFaultTemporary and ...
CC.longActive = CC.enabled and not any(e.overrideLongitudinal ...) and self.CP.openpilotLongitudinalControl
```

`CC.latActive` is already correct — it reads `selfdriveState.active`, which 1.2 keeps
true in standby. **No change needed for lateral.**

For longitudinal, we need carcontroller to NOT send ACC commands when in lat_only.
The cleanest place is carcontroller (Phase 1.3b), but we also need `CC.longActive` to
reflect MADS so the UI/plan planner behave correctly. Read the toggle in controlsd:
```python
# At init:
self.mads_enabled = ...  # same file read as 1.2

# In state_control, after CC.latActive line:
if self.mads_enabled:
    # In lat_only standby, openpilot stays engaged for lateral but releases long
    lat_only_standby = self.sm['selfdriveState'].active and not CS.cruiseState.enabled
    if lat_only_standby:
        CC.longActive = False
```
**Note:** `CS.cruiseState.enabled` here is the *stock* ACC state (unlatched). When
stock ACC is off but openpilot is active (lat_only), we force `longActive=False`.

### Phase 1.3b — carcontroller.py: don't send ACC in standby
**File: `opendbc_repo/opendbc/car/proton/carcontroller.py:175-182`** — the
`create_acc_cmd` call. Gate `longActive` on not-in-standby:
```python
# At init, read the toggle (same file pattern)

# In the apply_torque / send block (~line 178):
mads_standby = self.mads_enabled and CC.latActive and not CS.out.cruiseState.enabled
can_sends.append(
    create_acc_cmd(
        self.packer,
        accel_cmd,
        CC.longActive and not mads_standby,   # suppress ACC in MADS standby
        CS.out.gasPressed and CS.out.cruiseState.enabled,
        CS.out.standstill,
        CS.stock_acc_cmd_values,
    )
)
```
When `longActive=False`, `create_acc_cmd` (protoncan.py) passes through stock values —
the car coasts on the driver's pedal. This is the "gas fully manual" MADS behavior.

**Important:** steering (LKAS, line 137/151) already gates on `CC.latActive`, which
stays true in standby. No change needed for the steering path.

---

## Phase 1.4 — The empirical test (decides whether firmware work is needed)

After deploying Phase 1.1-1.3 (Python only, no rebuild), test on device:

### Test A — static standby (car parked, engine on)
1. Arm cruise MAIN → verify lateral engages (green steering indicator)
2. Press gas pedal → verify lateral STAYS engaged, speed increases manually
3. Release gas → verify lateral still engaged (coasts)
4. Press brake → verify FULL disengage
5. Re-arm MAIN → verify lateral re-engages
6. Press SET → verify normal ACC engages (lat + long)

### Test B — the controlsMismatch test (THE critical one)
While in standby (MAIN armed, no SET, lateral engaged) for >10 seconds, monitor:
```bash
# On device — watch the mismatch counter
python3 -c "
import cereal.messaging as m
s = m.sub_sock('pandaStates')
import time; time.sleep(2)
while True:
  msg = s.receive()
  if msg:
    for ps in msg.pandaStates:
      print(f'controlsAllowed={ps.controlsAllowed}')
"
```
- **If `controlsAllowed` stays True for >5 sec in standby** → ✅ **hypothesis
  confirmed, no firmware needed, MADS works.** Stop here.
- **If `controlsAllowed` drops to False after ~3 sec** → ❌ firmware guards still
  fire despite openpilot reporting engaged. Note the exact failure mode and move to
  Phase 2 evaluation (see Decision Gate below).

### Test C — driving test (low-speed, empty road)
Only after Test B passes. ~30 km/h empty straight road:
1. Arm MAIN → steering engages
2. Modulate gas manually → steering stays on, speed follows foot
3. Drive 5+ minutes → no unexpected disengagements
4. Brake → clean disengage
5. SET button → normal ACC works

---

## Decision Gate: if Test B fails

If `controlsAllowed` drops in standby, we have three options (in order of preference):

1. **Accept the limitation** — Kommu's own dev abandoned MADS on KA2 for exactly this
   reason. The safety cost of bypassing the firmware guards (reverting to KA1-style
   permissive firmware) is high. "MADS doesn't work on KA2" may be the honest answer.
2. **Port sunnypilot's `controls_allowed_lateral` parallel global** — the
   battle-tested solution. Adds a second safety global + second heartbeat bit
   (`send_heartbeat(engaged, engaged_mads)`) + rewrites lateral.h to gate on
   `controls_allowed || controls_allowed_lateral`. Weeks of careful C + Python work
   across panda firmware, pandad C++, and selfdrived. Real risk.
3. **Narrow firmware carve-out** — add a `PROTON_SAFETY_PARAM_LAT_ONLY` bit that makes
   proton.h skip `pcm_cruise_check` AND makes main.c's heartbeat counter tolerant.
   Smaller than option 2 but still touches firmware (boot-loop risk, signing keys).

**Default recommendation if Test B fails: option 1 (shelve).** The cost/benefit of
firmware work for a feature Kommu themselves abandoned is hard to justify.

---

## Files to modify (Phase 1, all Python)

| File | Change |
|---|---|
| `selfdrive/appbridged/appbridged.py` | MADS toggle (file-based `/data/params/MadsEnabled`) |
| `selfdrive/selfdrived/selfdrived.py` | `lat_only` flag + gas gate + event stripping |
| `selfdrive/controls/controlsd.py` | Force `CC.longActive=False` in standby |
| `opendbc_repo/opendbc/car/proton/carcontroller.py` | Suppress `create_acc_cmd` in standby |
| `kommu-drive/.../SettingsSheet.swift` | MADS toggle row |
| `kommu-drive/.../DeviceSettings.swift` | `madsEnabled` field |

**Files NOT touched:** `proton.h`, `main.c`, `panda_h7.bin.signed`, `bootstub`,
`params_keys.h` (no Params key = no `params_pyx.so` rebuild).

## Deploy (no rebuild, no firmware)
```bash
# scp the 4 Python files, then on device:
pkill -f selfdrived; pkill -f controlsd
# (manager auto-restarts them)
# Toggle MADS on via the iOS app (writes /data/params/MadsEnabled)
```

## Revert
```bash
rm /data/params/MadsEnabled
git checkout HEAD -- selfdrive/appbridged/appbridged.py \
  selfdrive/selfdrived/selfdrived.py \
  selfdrive/controls/controlsd.py \
  opendbc_repo/opendbc/car/proton/carcontroller.py
pkill -f selfdrived; pkill -f controlsd
```

---

## Safety integrity (unchanged by MADS)

These all still fire and disengage lateral:
- **Firmware tx_hook:** LKAS bit consistency, torque cap (≤599), zero-torque-when-inactive
- **Firmware:** CAN lag disengage, heartbeat timeout
- **openpilot:** all IMMEDIATE_DISABLE events (steer fault, CAN error, door open, seatbelt, wrong gear)
- **openpilot:** driver steering override (suspends lateral, resumes on release)
- **Driver:** brake pedal ALWAYS disengages (user's explicit choice)

The only thing MADS removes: **gas pedal as a lateral disengage trigger.** This is the
intended behavior — the driver is intentionally using the gas manually in standby.

---

## Background — why not the old plan or the other forks

### Old plan (deprecated 2026-08-09, see tools-local/MADS-IMPLEMENTATION-PLAN.md)
Tried to bypass `pcm_cruise_check` in proton.h. Two problems: (1) doesn't solve the
main.c:202 heartbeat-engaged counter, (2) Kommu abandoned the same approach
(`proton_mads` branch, unmerged 5 months). Faking `cruiseState.enabled=True` also has
wide blast radius (both production forks avoid it).

### sunnypilot MADS
Full rewrite: 5-state machine, custom cereal schema, custom firmware safety C
(`controls_allowed_lateral` parallel global + second heartbeat bit + rewritten
lateral.h). Battle-tested but thousands of lines across Python + C + pandad C++. No
Proton support. Overkill for our case if the Phase 1 hypothesis holds.

### FrogPilot Always-On Lateral (the inspiration for this plan)
Thin layer: force `CC.latActive` in controlsd, set stock `ALWAYS_ON_LATERAL` flag.
~30 lines. But the stock flag doesn't exist in our 0.10.3 firmware, so we adapt the
*mechanism* (override at the selfdrived event layer) rather than the flag.

### Why this plan might work where the old one couldn't
The old plan kept openpilot disengaged and tried to bypass firmware guards. This plan
keeps openpilot **engaged** (via event stripping), so the panda heartbeat reports
`engaged=true`, and the firmware guards never fire. The bet: Proton's tx_hook doesn't
gate steering on `controls_allowed` (verified), so keeping openpilot engaged is
sufficient. Phase 1.4 Test B confirms or refutes this empirically before any firmware
work is considered.
