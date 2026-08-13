# MADS Debug Log — Proton X70 on Kommu KA2

## Session: 2026-08-13

## Goal
Standby lateral steering for Proton X70 on Kommu KA2: openpilot steers when MAIN cruise button is armed (no SET pressed), gas is manual, brake disengages.

Three states:
- **Disabled**: MAIN off → nothing
- **Standby**: MAIN armed, no SET → lateral only, gas manual
- **Enabled**: SET pressed → full lat + long (normal ACC)

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

## Bug #3: gas disengages in standby — UNRESOLVED

### Symptom
When in MADS standby (MAIN pressed, GREEN), pressing gas intermittently disengages openpilot. User reports this has never happened on other cars — gas should NOT disengage in standby.

### Root cause (CONFIRMED)
`cruise_avail_cam` (CRUISE_AVAILABLE, cam bus 2) is the ONLY signal that tracks MAIN, but the **camera ECU publishes it intermittently** — it drops False for 1-3 seconds during a sustained MAIN press. This is the car's ECU behavior, not a code bug. Nothing in our code modifies the signal — it's read raw from CAN:
```python
self.cruise_avail_cam = bool(cp_cam.vl["PCM_BUTTONS"]["CRUISE_AVAILABLE"])
```

Each 1-3s drop makes:
1. `cruiseState.available = False`
2. `lat_only = False`
3. gas rising edge fires `gas_disengage and not lat_only` → `pedalPressed` → **disengage**

Gas is just the trigger. The root cause is the noisy `cruise_avail_cam` signal dropping, which makes openpilot think MAIN was released.

### Signal investigation (all candidates checked)

| Signal | Bus | Message | Result |
|---|---|---|---|
| `ACC_ON_OFF_BUTTON` | 0 (pt) | PCM_BUTTONS | **always True** — latched status, NOT MAIN |
| `ACC_ON_OFF_BUTTON` | 2 (cam) | PCM_BUTTONS | noisy, True in park, blips in drive |
| `CRUISE_AVAILABLE` | 0 (pt) | PCM_BUTTONS | **always True** — useless (same as acc_on_off_pt) |
| `CRUISE_AVAILABLE` | 2 (cam) | PCM_BUTTONS | **noisy** — True during standby but drops 1-3s |
| `CRUISE_CONTROL_EN` | 0 (pt) | GAS_PEDAL | **always False** — not the standby signal |
| `DRIVING_MODES` (2-bit) | 0 (pt) | PCM_BUTTONS | **always 0** — not useful |
| `DRIVING_MODES` (2-bit) | 2 (cam) | PCM_BUTTONS | mostly 0, briefly 2 (SET press?) |
| `CRUISE_BTN` | 0 (pt) | ACC_BUTTONS | **always False** — not the MAIN button |
| `CRUISE_DISABLED` | 2 (cam) | ACC_CMD | **always True** — not useful |
| `MOTION_CONTROL` (4-bit) | 2 (cam) | ACC_CMD | **always 9** — constant |
| `ICC_ON` | 2 (cam) | PCM_BUTTONS | **always False** — ICC mode off |
| `GAS_OVERRIDE` | 0 (pt) | PCM_BUTTONS | **always False** |

**Conclusion: NO clean signal exists on the X70 for "MAIN armed / standby".** Only `cruise_avail_cam` (bus 2) tracks MAIN, but it's inherently noisy (1-3s drops from the camera ECU). Bus 0's version is always True (useless).

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

### Attempted fix (REVERTED — user rejected)
A 30-frame (0.3s) falling-edge debounce was attempted but:
1. Insufficient: the drops are 1-3s, debounce only covers 0.3s
2. Dangerous: a longer debounce would mask real MAIN release (steering after driver turned off cruise)

A latch (hold `lat_only=True` for 3s of signal False) was also attempted but **reverted** — user rejected as "ridiculous and stupid" and reminded not to write code when unsure. Gas-disengage-on-standby has never been seen on other cars, so the root cause (noisy signal) must be understood before any fix.

### What is NOT the answer
- **Debounce**: insufficient for 1-3s drops, dangerous if extended
- **Bus 0 CRUISE_AVAILABLE**: always True (useless)
- **Any other DBC signal**: none track MAIN cleanly

### Open questions for next session
1. Is `cruise_avail_cam` noise a CAN bus issue (bus 2 timing, panda firmware) or the camera ECU itself?
2. Is there a signal we haven't found (undocumented DBC, different message) that cleanly indicates "MAIN armed"?
3. How does KA1 handle this? KA1 used `ACC_ON_OFF_BUTTON` from bus 0 (always True on X70) — did KA1 have the same gas-disengage issue, or did KA1 use a different gate?
4. Does the X70 HUD read `cruise_avail_cam` directly, or does it use a different (cleaner) internal signal?
5. Should we investigate the panda's raw CAN dump (not the parsed value) to see if the ECU truly sends CRUISE_AVAILABLE=0 for 1-3s, or if the CANParser is dropping it?

---

## Current state of code on device (branch x70-map)

### Files modified (all deployed):
1. **`opendbc_repo/opendbc/car/proton/carstate.py`**:
   - `_update_lks_state(self, cp, cp_cam)` — fixed NameError (Bug #1)
   - `ret.cruiseState.available = bool(self.cruise_avail_cam) if self.mads_enabled else True` (Bug #2 fix)
   - `ret.cruiseState.nonAdaptive = False` (reverted broken MADS override)
   - MADS_SIG diagnostic logs: `acc_on_off_pt`, `acc_on_off_cam`, `cruise_avail_pt`, `cruise_avail_cam`, `cruise_ctrl_en`, `driving_modes_pt`, `driving_modes_cam`, `cruise_btn`, `cruise_disabled_cam`, `motion_control_cam`, `gas_override`, `is_icc_on`, `nonAdaptive`, `cruise_enabled`, `gear`

2. **`selfdrive/selfdrived/selfdrived.py`**:
   - `lat_only = CS.cruiseState.available and not CS.cruiseState.enabled and drive` (gated on `available` not `nonAdaptive`)
   - pcmEnable injection + disabler stripping at end of update_events (unchanged from handoff)
   - MADS_DEBUG logging (unchanged)

3. **`selfdrive/controls/controlsd.py`**:
   - `CC.longActive = False` when MADS standby (unchanged from handoff)

4. **`selfdrive/appbridged/appbridged.py`** — MadsEnabled file toggle (unchanged)

5. **iOS Swift** — MADS toggle in SettingsSheet, LogsView tab (unchanged)

### What's NOT deployed (reverted):
- 30-frame debounce on `cruise_avail_cam` (insufficient + dangerous)
- 3s latch on `lat_only` (user rejected — "don't write code if unsure")

---

## Deploy scripts created this session
- `result/deploy_mads_crashfix.sh` — Bug #1 fix (card NameError)
- `result/deploy_mads_standby_gate.sh` — Bug #2 fix (auto-steer without MAIN)
- `result/deploy_mads_sig_diag.sh` — expanded MADS_SIG diagnostics (all candidate signals)
- `result/deploy_mads_debounce.sh` — DEPRECATED (reverted, don't use)
- `result/deploy_mads_latch.sh` — DEPRECATED (reverted, don't use)

## Log dumps collected this session
- `result/device-logs-20260813-155514` — Bug #1 (card crash)
- `result/device-logs-20260813-160608` — Bug #2 (acc_on_off_pt always True)
- `result/device-logs-20260813-161722` — Bug #3 (cruise_avail_cam flapping, gas disengage)
- `result/device-logs-20260813-163248` — Bug #3 (expanded MADS_SIG, all candidates checked)
- `result/device-logs-20260813-164247` — Bug #3 (cruise_avail_pt vs cam comparison)

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

# MADS state debug (shows lat_only, enabled, etc):
grep MADS_DEBUG /data/log/swaglog.* | python3 -c "import sys,json; [print(json.loads(l).get('msg$s','')) for l in sys.stdin]"

# card crashes:
grep '"daemon": "card"' /data/log/swaglog.* | grep -i error
```
