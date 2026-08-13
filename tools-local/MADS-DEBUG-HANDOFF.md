# MADS Implementation Status — 2026-08-13

## Goal
Standby lateral steering for Proton X70 on Kommu KA2: openpilot steers when MAIN cruise button is armed (no SET pressed), gas is manual, brake disengages.

Three states:
- **Disabled**: MAIN off → nothing
- **Standby**: MAIN armed, no SET → lateral only, gas manual
- **Enabled**: SET pressed → full lat + long (normal ACC)

## What's deployed on device (branch x70-map, commit ccfa85f99)

### Files modified:
1. **`opendbc_repo/opendbc/car/proton/carstate.py`** — MADS toggle read, MAIN signal detection, `nonAdaptive` field, diagnostic logging
2. **`selfdrive/selfdrived/selfdrived.py`** — lat_only computation, pcmEnable injection (end of update_events), event stripping, gas gate, mismatch_counter skip, debug logging
3. **`selfdrive/controls/controlsd.py`** — CC.longActive=False in standby
4. **`selfdrive/appbridged/appbridged.py`** — MadsEnabled file toggle
5. **iOS Swift** — MADS toggle in SettingsSheet, LogsView tab

### How it works (current code):
- **carstate.py**: When `mads_enabled=True`, sets `ret.cruiseState.nonAdaptive = not (acc_on_off or gas_override)`. Reads `ACC_ON_OFF_BUTTON` and `GAS_OVERRIDE` from powertrain bus (`cp`, bus 0). Also reads same signals from camera bus (`cp_cam`, bus 2) for diagnostics.
- **selfdrived.py**: `lat_only = not CS.cruiseState.nonAdaptive and not CS.cruiseState.enabled and gear==drive`. When lat_only=True: injects `pcmEnable` (at END of update_events, after events.clear()), strips `pcmDisable`/`buttonCancel`/`cruiseDisabled`, skips mismatch_counter for controls_allowed.
- **controlsd.py**: Forces `CC.longActive=False` when MADS standby (lat_active but cruise not enabled).

## Current problem: MAIN button signal detection

The `MADS_SIG` diagnostic log is deployed and will show:
```
MADS_SIG: acc_on_off_pt=? acc_on_off_cam=? cruise_avail_cam=? gas_override=? is_icc_on=? nonAdaptive=? cruise_enabled=? gear=?
```

**The issue:** We don't know which CAN signal goes True when the user presses the MAIN cruise button on the X70. Tried:
1. `ICC_ON` from camera bus → **DOES NOT work** (it's a mode selector, not MAIN button). User got "wrong cruise mode" error because `nonAdaptive=True` (is_icc_on was False even with MAIN pressed).
2. `ACC_ON_OFF_BUTTON` from powertrain bus (KA1 pattern) → **UNTESTED** — might not exist on bus 0 for X70.
3. `CRUISE_AVAILABLE` from camera bus (proton_mads pattern) → **UNTESTED** — proton_mads used this but was abandoned for firmware reasons, not signal reasons.

**After the next drive, check the MADS_SIG logs to see which signal goes True when MAIN is pressed.** Then update `nonAdaptive` in carstate.py to use that signal.

## Key architecture facts (KA2, 0.10.3)

### Two-daemon split:
- `selfdrived.py` → computes enabled/active from events via state machine, publishes `selfdriveState`
- `controlsd.py` → reads `selfdriveState.active` → sets `CC.latActive` (steering), `CC.longActive` (ACC)
- `pandad.cc:464` → heartbeat: `engaged = selfdriveState.getEnabled()` → USB 0xf3 → firmware `heartbeat_engaged`

### CAN buses:
- **Bus 0 (powertrain/main_bus)** — between panda and car ECUs
- **Bus 2 (camera/cam_bus)** — between panda and camera ECU
- `cp = can_parsers[Bus.pt]` reads bus 0
- `cp_cam = can_parsers[Bus.cam]` reads bus 2
- PCM_BUTTONS (addr 419) is subscribed on BOTH buses (we added it to pt parser)

### PCM_BUTTONS signals (addr 419, from proton_general_pt.dbc):
| Signal | Bit | Bus | Meaning |
|---|---|---|---|
| `GAS_OVERRIDE` | 18 | pt+cam | Gas pedal pressed (KA1 uses this) |
| `ACC_ON_OFF_BUTTON` | 16 | pt+cam | MAIN button arm signal (KA1 uses this from bus 0) |
| `ICC_ON` | 20 | cam | ICC mode selector (NOT the MAIN button — mode only) |
| `CRUISE_AVAILABLE` | 17 | cam | Cruise available status (proton_mads uses this from bus 2) |

### Proton firmware safety (proton.h):
- **tx_hook does NOT gate steering on `controls_allowed`** — only checks LKAS bit consistency + torque cap ≤599. This is why MADS CAN work without firmware changes: steering torque passes even when `controls_allowed=false`.
- **rx_hook calls `pcm_cruise_check`** which clears `controls_allowed` when stock ACC drops (reads CAN bus 2 directly — Python can't prevent this).
- **main.c:202 heartbeat counter** — clears `controls_allowed` if `controls_allowed && !heartbeat_engaged` for 3 ticks. But since selfdrived keeps `enabled=True` during MADS, `heartbeat_engaged` stays true, so this doesn't fire.

## How to check logs after a drive:

```bash
# Via SSH MCP (kommu-ssh at 127.0.0.1:8787):
# MADS signal diagnostic (shows raw CAN values):
grep MADS_SIG /data/log/swaglog.* | python3 -c "import sys,json; [print(json.loads(l).get('msg$s','')) for l in sys.stdin]"

# MADS state debug (shows lat_only, enabled, etc):
grep MADS_DEBUG /data/log/swaglog.* | python3 -c "import sys,json; [print(json.loads(l).get('msg$s','')) for l in sys.stdin]"

# selfdrived errors:
grep '"daemon": "selfdrived"' /data/log/swaglog.* | python3 -c "
import sys,json
for l in sys.stdin:
    d=json.loads(l)
    m=d.get('msg\$s',str(d.get('msg',''))[:200])
    print(f\"[{d.get('level','?')}] {m}\")"
```

## KA1 vs proton_mads vs our approach

| | KA1 (worked) | proton_mads (abandoned) | Our KA2 |
|---|---|---|---|
| **MAIN signal** | `ACC_ON_OFF_BUTTON` | `CRUISE_AVAILABLE` | `ACC_ON_OFF_BUTTON` (untested) |
| **Bus** | bus 0 (powertrain) | bus 2 (camera) | bus 0 (powertrain) |
| **Available** | `bool(gas_override or ACC_ON_OFF_BUTTON)` | `bool(CRUISE_AVAILABLE)` | `True` (hardcoded, unchanged) |
| **Enabled** | `bool(is_cruise_latch)` (latched) | `stock_enabled OR lat_only` | stock ACC_REQ computation (unchanged) |
| **Engagement** | Fakes `cruiseState.enabled=True` via latch | Fakes `cruiseState.enabled` via `lat_only` OR | Injects `pcmEnable` in selfdrived events |
| **Firmware** | SAFETY_ALLOUTPUT (no safety at all) | Custom safety_proton.h (neutered) | Stock KA2 proton.h (strict, but tx_hook doesn't gate steering) |

## What needs to happen:
1. Drive with MADS enabled and MAIN pressed
2. Check `MADS_SIG` log — which signal goes True?
3. Update `nonAdaptive` in carstate.py to use the correct signal
4. Verify standby lateral works (MADS_DEBUG should show `lat_only=True enabled=True`)
