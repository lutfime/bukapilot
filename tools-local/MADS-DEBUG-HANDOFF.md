# MADS Debug Log — Proton X70 on Kommu KA2

## Sessions: 2026-08-13 → 2026-08-14

## Goal
Standby lateral steering for Proton X70 on Kommu KA2: openpilot steers when MAIN cruise button is armed (no SET pressed), gas is manual, brake disengages.

Three states (target — not fully enforced for long yet):
- **Off**: MAIN off → nothing
- **Standby**: MAIN armed, no SET → lateral only, driver gas/brake; **OP must never command long**
- **ACC**: SET pressed → full lat + long (normal ACC)

**2026-08-14 status:** Bugs #1–#2 fixed. Bug #3 mitigated via `lat_only` HOLD (noisy MAIN).
Bug #4 (OP long in standby) diagnosed; proper Off/Standby/ACC long gate **not implemented yet**
(user: notes only, no room for dangerous one-liner shortcuts).

---

## Bug #1: card crashed (NameError) — FIXED

### Symptom
Drive enable cruise did nothing. Solid **blue** LED. MAIN/SET unresponsive.

### Root cause
`card.py` crashed on the FIRST `CarState.update()` with:
```
NameError: name 'cp' is not defined
  File ".../opendbc/car/proton/carstate.py", line 95, in _update_lks_state
    self.acc_on_off = bool(cp.vl["PCM_BUTTONS"]["ACC_ON_OFF_BUTTON"])
```

The MADS diagnostic reads (`acc_on_off`, `gas_override`) were added inside `_update_lks_state(self, cp_cam)`, which only receives the **camera** parser. `cp` (powertrain, bus 0) is a local in `update()` and not in scope. `card` is NOT `restart_if_crash` (`process_config.py` line 102), so it stayed dead the whole onroad session → no carState → no engagement → blue LED.

### Fix
Pass `cp` into `_update_lks_state(self, cp, cp_cam)` and update the call site `self._update_lks_state(cp, cp_cam)`. Both `opendbc_repo/` and `opendbc/` trees (hardlinked, identical).

### Deploy
`result/deploy_mads_crashfix.sh`

### Logs
`result/device-logs-20260813-155514/logs/swaglog.0000023401` line 136 (the crash).

---

## Bug #2: auto-steer without MAIN — FIXED

### Symptom
After Bug #1 fix, auto-steer engaged the moment you shifted to DRIVE, WITHOUT pressing MAIN.

### Root cause
`acc_on_off_pt` (ACC_ON_OFF_BUTTON, bus 0) is **always True on the X70** — 116/116 samples in `MADS_SIG`, even in park. It's a latched status, NOT the MAIN button. The old logic:
```python
ret.cruiseState.nonAdaptive = not (self.acc_on_off or self.gas_override)  # = not (True or False) = False
```
made `nonAdaptive=False` always → `lat_only = not False and not enabled and drive = True` → engaged immediately in drive.

### Fix
1. `carstate.py`: `ret.cruiseState.available = bool(self.cruise_avail_cam) if self.mads_enabled else True` — gate on CRUISE_AVAILABLE (cam bus 2) which tracks MAIN.
2. `carstate.py`: revert `nonAdaptive = False` (remove broken `acc_on_off_pt` gate).
3. `selfdrived.py`: `lat_only = CS.cruiseState.available and not CS.cruiseState.enabled and drive` — gate on `available` instead of `not nonAdaptive`.

### Deploy
`result/deploy_mads_standby_gate.sh`

### Logs
`result/device-logs-20260813-160608` — `MADS_SIG` shows `acc_on_off_pt=True` always, `cruise_avail_cam` True only during MAIN press.

---

## Bug #3: gas disengages in standby — MITIGATED (HOLD), not a clean-signal fix

### Symptom
When in MADS standby (MAIN pressed, GREEN), pressing gas intermittently disengages openpilot. User reports this has never happened on other cars — gas should NOT disengage in standby.

### Root cause (CONFIRMED)
`cruise_avail_cam` (CRUISE_AVAILABLE, cam bus 2) is the ONLY signal that tracks MAIN, but the **camera ECU publishes it intermittently** — it drops False for 1-3 seconds during a sustained MAIN press. This is the car's ECU behavior, not a code bug. Nothing in our code modifies the signal — it's read raw from CAN:
```python
self.cruise_avail_cam = bool(cp_cam.vl["PCM_BUTTONS"]["CRUISE_AVAILABLE"])
```

Each 1-3s drop (without HOLD) made:
1. `cruiseState.available = False`
2. `lat_only = False`
3. gas rising edge fires `gas_disengage and not lat_only` → `pedalPressed` → **disengage**

Gas is just the trigger. The root cause is the noisy `cruise_avail_cam` signal dropping, which makes openpilot think MAIN was released.

### Mitigation in tree (NOT a clean MAIN signal)
`selfdrived` `lat_only` HOLD: once True, hold through `available` noise; clear on gear≠drive,
or `!enabled && !available`, or `available=False` >4s while enabled. Still a timeout-based
compromise — user prefers no magic numbers long-term if a clean MAIN is found.

### Signal investigation (all candidates checked)

| Signal | Bus | Message | Result |
|---|---|---|---|
| `ACC_ON_OFF_BUTTON` | 0 (pt) | PCM_BUTTONS | **always 0** (MADS_BIT 2026-08-14) — not MAIN; old "always True" obsolete |
| `ACC_ON_OFF_BUTTON` | 2 (cam) | PCM_BUTTONS | often 1 when MAIN off; not the standby arm bit |
| `CRUISE_AVAILABLE` | 0 (pt) | PCM_BUTTONS | **always 0** (MADS_BIT) — useless for MAIN |
| `CRUISE_AVAILABLE` | 2 (cam) | PCM_BUTTONS | **MAIN tracker** — True in standby, noisy 1-3s drops |
| `CRUISE_CONTROL_EN` | 0 (pt) | GAS_PEDAL | **always False** — not the standby signal |
| `DRIVING_MODES` (2-bit) | 0 (pt) | PCM_BUTTONS | **always 0** — not useful |
| `DRIVING_MODES` (2-bit) | 2 (cam) | PCM_BUTTONS | mostly 0, briefly 2 (SET press?) |
| `CRUISE_BTN` | 0 (pt) | ACC_BUTTONS | **always False** — not the MAIN button |
| `CRUISE_DISABLED` | 2 (cam) | ACC_CMD | **always True** — not useful |
| `MOTION_CONTROL` (4-bit) | 2 (cam) | ACC_CMD | **always 9** — constant |
| `ICC_ON` | 2 (cam) | PCM_BUTTONS | **always False** — ICC mode off |
| `GAS_OVERRIDE` | 0 (pt) | PCM_BUTTONS | **always False** |

**Conclusion: NO clean signal exists on the X70 for "MAIN armed / standby".** Only `cruise_avail_cam`
(bus 2 bit 17) tracks MAIN, and it's noisy (1-3s drops). Bus 0 b16/b17 are real **0** (see MADS_BIT
below) — useless for MAIN. Older table rows saying bus 0 "always True" are **obsolete**.

### `cruise_avail_cam` noise pattern (from 20260813-164247 logs)
```
9880-9902: True  (22s standby, MAIN held)
9902-9903: False (1s glitch)
9903-9908: True  (5s, driving_modes_cam=2 — SET pressed?)
9908-9909: False (1s glitch)
9909-9940: True  (31s standby)
9940-9942: False (2s glitch)
9942-9948: True  (6s)
9948-9951: False (3s glitch)
```
The False periods are 1-3 seconds — too long for a simple debounce, too short to be a real MAIN release.

---

## CRITICAL FINDING (2026-08-13): DBC bit naming KA1 vs KA2

### DBC bit positions / names differ between KA1 and KA2

The PCM_BUTTONS message (addr 419) defines ACC_ON_OFF_BUTTON and CRUISE_AVAILABLE
at **different bit positions / names** in KA1 vs KA2 DBC files:

| Bit | KA1 DBC (`ka1s_snapshot`) | KA2 DBC (current) |
|---|---|---|
| **16** | `ACC_SET` | `ACC_ON_OFF_BUTTON` |
| **17** | `ACC_ON_OFF_BUTTON` | `CRUISE_AVAILABLE` |

**Same physical bits, different names.** KA2's cam bit 17 (`CRUISE_AVAILABLE`) is the MAIN tracker
(same physical bit KA1 called `ACC_ON_OFF_BUTTON`). proton_mads also used cam `CRUISE_AVAILABLE`.

### MADS_BIT probe (2026-08-14) — NaN theory DISPROVEN

`MADS_BIT` logs raw b16/b17 on pt+cam with `ts_nanos` / NaN / UNSEEN checks
(`result/device-logs-20260814-082039`).

| Signal | Bus | Result |
|---|---|---|
| b16 (`ACC_ON_OFF_BUTTON`) | pt (0) | always **0** (real frames, not NaN/UNSEEN) |
| b17 (`CRUISE_AVAILABLE`) | pt (0) | always **0** (real frames) |
| b16 | cam (2) | often **1** when MAIN not armed |
| b17 | cam (2) | **1** when MAIN standby (`avail=True`) |

**Conclusion:** PCM_BUTTONS **does** arrive on bus 0. Earlier "always True on bus 0" was a bad
`bool()` / old-path artifact — **not** a clean MAIN on pt. **MAIN = cam b17 only.** No KA1-style
pt MAIN to fall back on. Stick with cam `CRUISE_AVAILABLE` + engagement HOLD for noise.

Debounce (0.3s) insufficient for 1–3s drops and dangerous if extended. Blind latch rejected by
user when unsure. Current tree uses `lat_only` HOLD (see below) for engagement/gas gate only.

### Remaining open questions (bits / MAIN)
1. Is cam b17 noise ECU vs CAN/parser? (raw CAN dump of addr 419 bus 2 still useful)
2. Any undocumented cleaner MAIN signal?
3. Why DBC renamed between KA1/KA2?

---

## Current state of code (local tree vs device — verify before assuming)

### In local working tree (as of 2026-08-14 notes update):
1. **`opendbc_repo/opendbc/car/proton/carstate.py`**:
   - NameError fix; `available` from cam `CRUISE_AVAILABLE` when MADS on
   - `MADS_BIT` diagnostic (b16/b17 pt+cam, ts/NaN/UNSEEN)
2. **`selfdrive/selfdrived/selfdrived.py`**:
   - `lat_only` HOLD SM (noise tolerance + 4s MAIN-off timeout)
   - pcmEnable inject + strip pcmDisable/buttonCancel/cruiseDisabled/wrongCarMode while `lat_only`
   - gas disengage skipped when `lat_only`; brake always disengages
   - `lat_only` stays True during full ACC (`cruise_enabled` does **not** clear it)
   - `lat_only` is **local only** — not published to cereal / not visible to controlsd
3. **`selfdrive/controls/controlsd.py`**:
   - Provisional long gate present in tree:
     `if mads and CC.enabled and not CS.cruiseState.enabled: CC.longActive = False`
   - **NOT accepted as final** (see Bug #4 / design note). Do not treat as "safe forever."
4. **appbridged / iOS** — MadsEnabled toggle (unchanged)

### Reverted / deprecated:
- 30-frame debounce on `cruise_avail_cam`
- Early latch attempts user rejected when unsure
- `result/deploy_mads_debounce.sh`, `deploy_mads_latch.sh` — DEPRECATED

### Deploy scripts
- `result/deploy_mads_crashfix.sh` — Bug #1
- `result/deploy_mads_standby_gate.sh` — Bug #2
- `result/deploy_mads_sig_diag.sh` — signal scan
- `result/deploy_mads_bit_diag.sh` — MADS_BIT probe
- `result/deploy_mads_long_fix.sh` — provisional long gate (do not ship as final without SET proof / SM)

### Log dumps
- `result/device-logs-20260813-*` — Bugs #1–#3
- `result/device-logs-20260814-082039` — MADS_BIT (MAIN=cam b17; pt bits 0); standby+gas; **no SET/full ACC** (`cruise_en=True` absent)

---

## Key architecture facts (KA2, 0.10.3) — from original handoff

### Two-daemon split:
- `selfdrived.py` → computes enabled/active from events via state machine, publishes `selfdriveState`
- `controlsd.py` → reads `selfdriveState.active` → sets `CC.latActive` (steering), `CC.longActive` (ACC)
- `pandad.cc:464` → heartbeat: `engaged = selfdriveState.getEnabled()` → USB 0xf3 → firmware `heartbeat_engaged`

### CAN buses:
- **Bus 0 (powertrain/main_bus)** — between panda and car ECUs
- **Bus 2 (camera/cam_bus)** — between panda and camera ECU
- `cp = can_parsers[Bus.pt]` reads bus 0
- `cp_cam = can_parsers[Bus.cam]` reads bus 2
- PCM_BUTTONS (addr 419) is subscribed on BOTH buses

### Proton firmware safety (proton.h):
- **tx_hook does NOT gate steering on `controls_allowed`** — only checks LKAS bit consistency + torque cap ≤599. This is why MADS CAN work without firmware changes: steering torque passes even when `controls_allowed=false`.
- **rx_hook calls `pcm_cruise_check`** which clears `controls_allowed` when stock ACC drops (reads CAN bus 2 directly — Python can't prevent this).
- **main.c:202 heartbeat counter** — clears `controls_allowed` if `controls_allowed && !heartbeat_engaged` for 3 ticks. But since selfdrived keeps `enabled=True` during MADS, `heartbeat_engaged` stays true, so this doesn't fire.

## How to check logs after a drive:

```bash
# MADS signal diagnostic (shows raw CAN values, all candidates):
grep MADS_SIG /data/log/swaglog.* | python3 -c "import sys,json; [print(json.loads(l).get('msg$s','')) for l in sys.stdin]"

# Bit probe (MAIN = cam b17):
grep MADS_BIT /data/log/swaglog.* | python3 -c "import sys,json; [print(json.loads(l).get('msg$s','')) for l in sys.stdin]"

# MADS state debug (shows lat_only, enabled, etc):
grep MADS_DEBUG /data/log/swaglog.* | python3 -c "import sys,json; [print(json.loads(l).get('msg$s','')) for l in sys.stdin]"

# card crashes:
grep '"daemon": "card"' /data/log/swaglog.* | grep -i error
```

---

## Bug #4: OP still commands gas/brake in standby (intermittent) — OPEN

**User clarification (important):** Not “driver foot gas works” (that is intended in standby).
Complaint is **openpilot still actuating longitudinal** sometimes while MAIN standby — dangerous.

### Root cause (code, high confidence)
`controlsd` builds:
```python
CC.longActive = CC.enabled and ... and openpilotLongitudinalControl
# then MADS clear — historically gated wrong:
if mads and CC.latActive and not CS.cruiseState.enabled:
    CC.longActive = False
```

MAIN / `lat_only` do **not** drive `latActive`. `latActive` is an actuator gate:

```python
CC.latActive = active and not steerFault* and (not standstill or steerAtStandstill)
```

Proton has no `steerAtStandstill`; standstill ≈ `vEgo ≤ 0.3 m/s`. So in standby with
`enabled=True` and stock ACC off, when you crawl/stop or get a temp steer fault:

1. `latActive` → False (steer pauses — expected)
2. MADS long clear **skipped** (because it required `latActive`)
3. `longActive` stays True → `create_acc_cmd` sends `ACC_REQ` + planner accel → **OP gas/brake**

Matches “not always, but sometimes” (stop-and-go). 1 Hz `MADS_DEBUG` won’t show frame-level blips;
need qlog `carControl.longActive` for proof.

### Why `lat_only` alone is NOT the long fix
User asked: “when `lat_only` active, no long — why not gate on that?”

| Flag | Owner | Meaning today |
|---|---|---|
| `lat_only` | selfdrived only | Survive MAIN noise; skip gas-disengage; keep OP enabled without stock ACC |
| `cruiseState.enabled` | carstate (stock ACC bits) | SET / full ACC |
| `latActive` | controlsd | Allowed to steer **this frame** |
| `longActive` | controlsd | Allowed to command ACC **this frame** |

`lat_only` **stays True during full ACC** on purpose (cancel→standby / noise). So
`if lat_only: longActive=False` would **kill OP long after SET** — wrong.

Bug #3 HOLD fixed **gas disengaging OP**, not **OP commanding long**. Different bug.

`lat_only` is also **not published** — controlsd cannot read it today.

### Provisional one-liner (in tree) — NOT final
```python
if mads and CC.enabled and not CS.cruiseState.enabled:
    CC.longActive = False
```
- Fixes standby long when `latActive` drops (good for Bug #4 symptom).
- **Risk:** if `cruiseState.enabled` flickers False during real SET/ACC, long drops briefly
  (jerk / ACC release). While *moving*, old `latActive && !cruise_en` gate already had that
  risk; new gate also applies at **standstill during ACC**.
- **Unproven on car:** latest bit dump had **no** `cruise_en=True` (no SET road data).
- User: leave **no room for dangerous error** → do **not** ship one-liner as final safety story.

### Required design: Off / Standby / ACC (not just `lat_only`)
Goal modes:

| Mode | Steer | OP long |
|---|---|---|
| **Off** | no | no |
| **Standby** (MAIN, no SET) | yes | **never** |
| **ACC** (SET) | yes | yes |

Today’s `lat_only` SM ≈ engagement/MAIN noise only — **Standby and ACC collapsed into one flag**.
Long is a separate raw-bit one-liner. Incomplete.

Safe long rule: **allow long only in ACC mode; force off in Standby and Off.**

Transitions should be explicit (prefer events / latched edges over trusting every frame of
`ACC_REQ`/`ACCEL_ALLOWED`):

- **→ ACC:** SET / rising `cruiseState.enabled` (latch once)
- **→ Standby:** cancel / stock ACC off while MAIN still armed
- **→ Off:** brake / MAIN off / gear / USER_DISABLE

If `cruise_en` is noisy in ACC, either prove stability on a SET drive, or latch ACC mode and
leave only on cancel/brake/disengage — **not** on a single False sample. Avoid blind long
timeouts if possible; any hold must be justified by measured noise, not guesswork.

**Do not implement yet** — notes only per user (2026-08-14).

---

## Architecture reminder (why layers confuse)

```
MAIN (cam b17) → cruiseState.available → lat_only HOLD (selfdrived)
                      ↓
              pcmEnable / strip events → enabled / active (selfdriveState)
                      ↓
controlsd: latActive = active ∧ ¬standstill∧…     ← NOT MAIN
           longActive = enabled ∧ …  then MADS gate ← must be Standby/ACC aware
                      ↓
Proton create_acc_cmd(longActive) → ACC_REQ / CMD on bus 0
```

---

## MADS standby brake SOUND in silent mode (2026-08-14) — ROOT CAUSE = logic, FIXED

**Symptom:** QuietMode (silent mode) ON. In MADS standby (MAIN armed, no SET), holding brake
plays a persistent refuse beep. Engaged+brake is correctly silent.

**Real root cause (logic, not sound):** the `lat_only` re-arm block in
`selfdrived.py:update_events` injected `pcmEnable` **every frame while not enabled — including
while the brake was held**:
```python
    if self.lat_only:
        if not self.enabled:                       # UNCONDITIONAL — no brake check
            self.events.add(EventName.pcmEnable)
```
So while braking: frame 1 brake → `USER_DISABLE` → OP disengages (disengage sound, already
silent in quiet mode). Frame 2..N (brake still held): `lat_only` still true → injects
`pcmEnable` → tries to re-engage → brake (`pedalPressed`) blocks it → typed `NO_ENTRY` →
`NoEntryAlert` → AudibleAlert **refuse**. refuse is excluded from the soundd quiet-mode gate
(`if quiet_mode and new_alert != AudibleAlert.refuse`) → beep every blocked attempt.

This only shows in standby because ONLY `lat_only` injects `pcmEnable` continuously. Full ACC
and MADS-off don't, so no persistent blocked-attempt beep there. User was right: "why does it
try to engage while braking?" — it shouldn't.

**Drive evidence (2026-08-14--00-45-49):** latActive while brake held = 2/19394 frames (~20 ms,
the disengage transition) → lateral is correctly BLOCKED during braking. After brake release,
latActive returns p50/p90 = 0.00 s → instant resume. So the fix must preserve resume-on-release.

**FIX (selfdrived.py, applied in working tree, uncommitted for review):** gate the re-arm
injection on "not braking":
```python
    if self.lat_only:
        if not self.enabled and not CS.brakePressed:   # don't attempt while braking
            self.events.add(EventName.pcmEnable)
```
- brake held → no engage attempt → no pedalPressed/noEntry → **no beep (fixed at the source)**
- brake released → pcmEnable resumes → lateral re-engages instantly (resume-on-release preserved)
- brake still disengages instantly via USER_DISABLE (path unchanged)

**soundd.py patch REVERTED.** An earlier symptom-level fix (suppress `pedalPressed/noEntry` in
quiet mode, commit 5395909) was reverted in the working tree — the selfdrived logic fix above is
the single clean fix. (Device still has the harmless soundd patch keeping it quiet until the
selfdrived fix is deployed; revert soundd on device at deploy time.)

---

## Next actions (when user says go — not now)
1. Implement real **Off / Standby / ACC** mode for long (publish from selfdrived or equivalent);
   force `longActive=False` in Standby only.
2. Road-test **SET/full ACC** with high-rate logging of `cruise_en` / `longActive` before trusting
   raw `!cruise_en` every frame.
3. Keep Bug #3 HOLD only until cleaner MAIN exists; prefer measured noise over magic timeouts.
4. DONE (logic fix): brake-during-standby re-arm gate in selfdrived.py — see
   "MADS standby brake SOUND" section above. Awaiting deploy + road test.
5. Refresh device deploy inventory so “what’s on car” matches notes.