# MADS Implementation Plan — Proton X70 on Kommu KA2

**Status:** PLANNED — not yet implemented. This document is the complete reference for
implementing MADS (Modular Assistive Driving System) standby-steering on the KA2.

**Last updated:** 2026-08-06
**Branch base:** `x70-test` (openpilot 0.10.3-based bukapilot)

---

## Table of Contents
1. [What MADS is — the X70 behavior we want](#1-what-mads-is)
2. [Why MADS worked on KA1 but not KA2 (out of the box)](#2-ka1-vs-ka2)
3. [The firmware wall — `controls_allowed` explained](#3-the-firmware-wall)
4. [Feasibility verdict: it IS possible on KA2](#4-feasibility-verdict)
5. [Risk analysis: brick vs boot-loop](#5-risk-analysis)
6. [The signing keys: recovery + storage](#6-signing-keys)
7. [Phase 1: Python-only changes (no firmware)](#phase-1)
8. [Phase 2: Firmware changes (app-only, recoverable)](#phase-2)
9. [Phase 3: Testing & validation](#phase-3)
10. [Revert / recovery procedure](#revert)
11. [What we will NEVER touch (the bootstub)](#never-touch)
12. [Full file reference](#file-reference)

---

<a id="1-what-mads-is"></a>
## 1. What MADS is — the X70 behavior we want

MADS for the Proton X70 means **openpilot steers the car while cruise is in STANDBY**,
with the gas pedal fully manual:

| Cruise state | Normal openpilot | With MADS |
|---|---|---|
| **enabled** (SET pressed) | Steering + speed control | Steering + speed control (same) |
| **standby** (MAIN armed, no SET) | Nothing — OP fully off | **Steering ON, gas is fully manual (driver controls speed)** |
| **disabled** (MAIN off) | Nothing | Nothing (settings removed) |
| **brake pressed** (any time) | Disengage everything | **Disengage everything** (safest — user's choice) |

**The user-facing experience:** Arm cruise with the MAIN button. openpilot starts
steering/centering immediately. You control speed with your foot. Press SET if you want
OP to also handle speed (ACC). Press brake to fully disengage.

This is the sunnypilot "standalone lateral" feature. It's also what Kommu's KA1 device
shipped for the Proton platform.

---

<a id="2-ka1-vs-ka2"></a>
## 2. Why MADS worked on KA1 but not KA2 (out of the box)

**KA1's safety firmware** (`panda/board/safety/safety_proton.h`, branches `proton_mads` /
`ka1s_snapshot`) is trivially permissive:
```c
static void proton_rx_hook(const CANPacket_t *to_push) {
  vehicle_moving = true;
  controls_allowed = true;   // ALWAYS true — never cleared
}
```

**KA2's safety firmware** (current `x70-test`, `opendbc_repo/opendbc/safety/modes/proton.h`)
is stricter — it ties `controls_allowed` to the stock ACC state via `pcm_cruise_check`:
```c
// proton.h lines 77-96
if ((bus == 2) && (addr == (int)PROTON_ACC_CMD)) {
  bool stock_engaged = GET_BIT(msg, 36U) || GET_BIT(msg, 38U);  // ACC_REQ || STANDSTILL_REQ
  // ... debounce counter ...
  bool pcm_engaged = stock_engaged || (saw_stock_engaged && off_count < 5);
  pcm_cruise_check(pcm_engaged);   // clears controls_allowed when stock ACC drops
}

// safety.h line 534
void pcm_cruise_check(bool cruise_engaged) {
  if (!cruise_engaged) { controls_allowed = false; }   // <-- kills standby
}
```

The KA1 firmware never clears `controls_allowed`, so the Python-side `lat_only` latch
(keeping `cruiseState.enabled = True`) worked forever. The KA2 firmware reads the stock
ACC state **directly from the camera CAN bus (bus 2)**, so no Python trick can fool it.

---

<a id="3-the-firmware-wall"></a>
## 3. The firmware wall — `controls_allowed` explained

There are **two** things to understand:

### 3a. The steering torque gate (NOT the problem)
The Proton `proton_tx_hook` does **NOT** check `controls_allowed`. The steering message
(addr 432, `ADAS_LKAS`) passes through whenever:
```c
if (LKAS_ENGAGED1 != LKAS_LINE_ACTIVE) violation = true;   // bits must match
if (!LKAS_ENGAGED1 && steer_cmd != 0) violation = true;    // zero torque when inactive
if (steer_cmd > 599) violation = true;                     // torque cap
```
**No `controls_allowed` check.** A unit test (`test_adas_lkas_steer_consistency`) sends
a steering frame with `controls_allowed=False` and asserts it passes. So the firmware
itself will allow standby steering.

### 3b. The `controlsMismatch` heartbeat check (THE problem)
`selfdrived.py` lines 497-502:
```python
if self.enabled and any(not ps.controlsAllowed for ps in self.sm['pandaStates']
       if ps.safetyModel not in IGNORED_SAFETY_MODES):
    self.mismatch_counter += 1
# (counter > threshold → EventName.controlsMismatch → IMMEDIATE_DISABLE)
```
When openpilot thinks it's enabled (`self.enabled = True`) but the panda firmware reports
`controlsAllowed = False`, this counter climbs and disengages after a few seconds.

**This is why MADS breaks on KA2:** In standby, the firmware sees `ACC_REQ = 0` on bus 2
→ after 5 frames (100ms debounce) sets `controls_allowed = false` → `selfdrived.py`
mismatch counter increments → `controlsMismatch` → disengage. No amount of Python-side
latching can prevent this because the firmware reads the camera bus directly.

### The fix
Modify `proton.h` so that when MADS is enabled (via a new safety param bit), the firmware
keeps `controls_allowed = true` regardless of stock ACC state — just like KA1 did.

**This does NOT weaken steering safety** because `controls_allowed` doesn't gate steering
torque on Proton (see 3a). It only feeds the heartbeat mismatch check. All the real
safety checks remain: LKAS bit consistency, torque cap, relay malfunction, all
`IMMEDIATE_DISABLE` events, CAN lag, heartbeat timeout.

---

<a id="4-feasibility-verdict"></a>
## 4. Feasibility verdict: it IS possible on KA2

| Requirement | Status | Evidence |
|---|---|---|
| Firmware is modifiable | ✅ | `proton.h` is custom Kommu code, compiled into `panda_h7.bin.signed` |
| Device accepts custom firmware | ✅ | Bootstub built with `-DALLOW_DEBUG`, accepts debug-signed images |
| Signing keys are available | ✅ | Recoverable from git history (commit `70b17dd8`) — verified matching cert.h |
| Signing toolchain works | ✅ | `sign.py` + RSA private key recoverable; build auto-signs |
| Device auto-flashes | ✅ | `pandad.py:flash_panda()` detects signature mismatch, reflashes on boot |
| Recovery if bad flash | ✅ (app) / ❌ (bootstub) | App: restore old binary, reboot. Bootstub: **no DFU path on KA2** |

**Verdict: MADS is implementable on KA2.** Requires app-firmware changes (recoverable)
+ Python changes. The bootstub must NEVER be touched.

---

<a id="5-risk-analysis"></a>
## 5. Risk analysis: brick vs boot-loop

| Scenario | Outcome | Recoverable? |
|---|---|---|
| New app firmware works correctly | MADS works | N/A |
| New app firmware has a bug | Bootstub **rejects** it (signature/checksum), pandad boot-loops, OP won't engage | ✅ Yes — restore old `panda_h7.bin.signed`, reboot |
| New app firmware has wrong signature | Bootstub rejects, boot-loop | ✅ Yes — same revert path |
| **Bootstub gets corrupted** | **Dead panda — device permanently unusable** | ❌ **NO — KA2 has no DFU recovery** |

**Critical rule:** We modify and reflash ONLY the app firmware (`panda_h7.bin.signed`).
The bootstub (`bootstub.panda_h7.bin`) lives in a separate flash sector and is the
gatekeeper that loads/validates the app. We NEVER rebuild or reflash it. As long as the
bootstub is untouched, the worst case is a recoverable boot-loop.

### Why KA2 can't recover a bricked bootstub
- `system/hardware/ka2/hardware.py` does NOT override `recover_internal_panda` /
  `reset_internal_panda` (inherits no-op `pass` from `HardwareBase`)
- Unlike comma.ai's TICI (which drives `STM_RST_N`/`STM_BOOT0` GPIOs to force DFU),
  KA2 has no hardware control over the STM32's DFU pins
- If the bootstub sector is corrupted, the chip cannot enter DFU mode, and there's no
  way to force it — permanent brick

### The boot-loop recovery procedure (must verify before flashing)
1. Back up the current working `panda_h7.bin.signed` (it's in git at the current commit)
2. Prepare the revert command: `git checkout HEAD -- panda/board/obj/panda_h7.bin.signed`
3. Flash the new firmware
4. If OP won't engage / device boot-loops: SSH in, restore the old binary, reboot pandad
5. pandad auto-flashes the known-good binary, device returns to working state

---

<a id="6-signing-keys"></a>
## 6. The signing keys: recovery + storage

### What was deleted
Commit `724cf630f` ("bukapilot release v10.0.5") deleted the entire signing infrastructure:
- `panda/certs/debug` (private RSA key — 2048 bit)
- `panda/certs/debug.pub` (public key — matches `cert.h`)
- `panda/certs/release.pub` (release public key)
- `panda/crypto/sign.py` (PKCS#1 v1.5 signing script)
- `panda/crypto/rsa.c`, `rsa.h`, `sha.c`, `sha.h`, `hash-internal.h`

### Recovery (all recoverable from commit `70b17dd8`)
```bash
# Verify the parent of the deletion commit has the files
git show 70b17dd8:panda/certs/debug       # → RSA private key (-----BEGIN RSA PRIVATE KEY-----)
git show 70b17dd8:panda/certs/debug.pub   # → ssh-rsa public (n0inv matches cert.h: 424155863)
git show 70b17dd8:panda/certs/release.pub # → ssh-rsa public
git show 70b17dd8:panda/crypto/sign.py    # → signing script
```
The recovered `debug.pub` was cryptographically verified: its Montgomery `n0inv` (424155863)
exactly matches the `debug_rsa_key` embedded in `panda/board/obj/cert.h`. This is the
genuine counterpart private key.

### Storage decision (user must choose)
**Recommended: keep the key LOCAL ONLY (on your Mac), never in any repo.**

| Option | Security | Convenience |
|---|---|---|
| **Local only (recommended)** | Key never leaves your Mac; even if GitLab repo leaks, no firmware signing possible | You must build+sign on your Mac, commit only the `.bin.signed` |
| Private GitLab repo | Anyone with repo access can sign firmware for your device | Build works anywhere the repo is cloned |

**With "local only":** the `.gitignore` should exclude `panda/certs/debug` (the private key).
You commit only the resulting `panda/board/obj/panda_h7.bin.signed`. The build process runs
on your Mac with the key present locally.

---

<a id="phase-1"></a>
## Phase 1: Python-only changes (no firmware)

These changes implement the full MADS logic in Python. They are **necessary but not
sufficient** — without Phase 2 firmware changes, standby steering won't survive the
`controlsMismatch` check. But Phase 1 alone is safe to test (it won't break anything,
and verifies all the engagement/latch logic is correct).

### Phase 1.1 — Toggle infrastructure

**File: `selfdrive/appbridged/appbridged.py`** — follow the existing `X70UsePidController` /
`UseSupercomboModel` pattern (file-based, `/data/params/` NOT `/data/params/d/`):

```python
MADS_TOGGLE_FILE = "/data/params/MadsEnabled"

def get_mads_toggle() -> bool:
    try:
        return open(MADS_TOGGLE_FILE).read().strip() == "1"
    except (FileNotFoundError, OSError):
        return False  # DEFAULT OFF — safe

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

**KommuDrive iOS app** — add a "MADS (beta)" toggle row (same pattern as the
"0.11 Model (beta)" toggle already added). Default OFF.

### Phase 1.2 — carstate.py: the `lat_only` latch

**File: `opendbc_repo/opendbc/car/proton/carstate.py`**

The latch keeps `cruiseState.enabled = True` after the user cancels stock ACC, so
openpilot's engagement state machine stays in lateral mode. Adapted from the working
`proton_mads` branch.

In `__init__`, add:
```python
self.prev_stock_enabled = False
self.lat_only = False
self.mads_enabled = False  # read once at init from toggle file
try:
    with open("/data/params/MadsEnabled") as f:
        self.mads_enabled = f.read().strip() == "1"
except (FileNotFoundError, OSError):
    self.mads_enabled = False
```

In `update()`, after computing `ret.cruiseState.enabled` (line ~199-201), add:
```python
# MADS latch: keep lateral engaged after user cancels stock ACC
if self.mads_enabled:
    stock_enabled = ret.cruiseState.enabled  # the value just computed from ACC_REQ etc.
    if stock_enabled or not ret.cruiseState.available or ret.gearShifter != car.CarState.GearShifter.drive:
        self.lat_only = False
    elif self.prev_stock_enabled:  # falling edge: user just cancelled ACC
        self.lat_only = True
    ret.cruiseState.enabled |= self.lat_only and ret.cruiseState.available
    self.prev_stock_enabled = stock_enabled
```

**How the latch works:**
- `lat_only` resets to False when: ACC is actually on, OR cruise unavailable, OR not in Drive
- `lat_only` sets to True ONLY on the falling edge of stock ACC (the frame right after
  the user cancels — detected via `prev_stock_enabled`)
- Once latched, `cruiseState.enabled` is forced True so openpilot keeps steering
- `lat_only` is a plain Python attribute, read directly by carcontroller

### Phase 1.3 — selfdrived.py: don't disengage on gas when MADS

**File: `selfdrive/selfdrived/selfdrived.py`**

Wrap the gas-pedal disengage in the MADS check. Currently (lines 213-216):
```python
if (CS.gasPressed and not self.CS_prev.gasPressed and self.disengage_on_accelerator) or \
  (CS.brakePressed and (not self.CS_prev.brakePressed or not CS.standstill)) or \
  (CS.regenBraking and (not self.CS_prev.regenBraking or not CS.standstill)):
    self.events.add(EventName.pedalPressed)
```

Change to (gas only — **brake still always disengages** per user's choice):
```python
mads_on = self.mads_enabled  # read at init from toggle file
gas_disengage = CS.gasPressed and not self.CS_prev.gasPressed and self.disengage_on_accelerator
if (gas_disengage and not mads_on) or \
  (CS.brakePressed and (not self.CS_prev.brakePressed or not CS.standstill)) or \
  (CS.regenBraking and (not self.CS_prev.regenBraking or not CS.standstill)):
    self.events.add(EventName.pedalPressed)
```

**Note:** `gasPressedOverride` (from `car_specific.py:141`) still fires — this keeps the
`OVERRIDE_LONGITUDINAL` event active, so longitudinal releases to driver while lateral
stays engaged. This is the correct MADS behavior.

### Phase 1.4 — carcontroller.py: suppress longitudinal commands when lat_only

**File: `opendbc_repo/opendbc/car/proton/carcontroller.py`**

Currently (line 178): `CC.longActive` drives the ACC command. When `lat_only` is True,
we must NOT send openpilot longitudinal commands (gas is manual). Change the
`create_acc_cmd` call:
```python
# Line ~178: gate long_active on not lat_only
can_sends.append(
    create_acc_cmd(
        self.packer,
        accel_cmd,
        CC.longActive and not CS.lat_only,   # <-- suppress longitudinal in MADS standby
        CS.out.gasPressed and CS.out.cruiseState.enabled,
        CS.out.standstill,
        CS.stock_acc_cmd_values,
    )
)
```

When `long_active=False`, `create_acc_cmd` (protoncan.py line 49) passes through stock
values — so the car just coasts on whatever the driver is doing with the pedal. This is
exactly the MADS "gas fully manual" behavior.

### Phase 1.5 — What Phase 1 does NOT do
Phase 1 does NOT change the firmware. Without Phase 2, standby steering will still be
killed by `controlsMismatch` after ~100ms. But Phase 1 is safe to deploy and test:
- Toggle defaults OFF → no behavior change for existing users
- All Python logic can be verified correct (engagement, latch, longitudinal suppression)
- Brake still disengages (user's explicit choice)
- Reverting Phase 1 is trivial (`git checkout`)

---

<a id="phase-2"></a>
## Phase 2: Firmware changes (app-only, recoverable)

**⚠️ PREREQUISITE:** Complete the boot-loop recovery procedure (Section 5) first. Back up
the current `panda_h7.bin.signed`. Verify the revert command works.

### Phase 2.1 — Recover the signing keys

```bash
cd /Users/lutfimacmini/Documents/Project/bukapilot

# Recover the signing toolchain from git history
mkdir -p panda/certs panda/crypto
git show 70b17dd8:panda/certs/debug > panda/certs/debug
git show 70b17dd8:panda/certs/debug.pub > panda/certs/debug.pub
git show 70b17dd8:panda/certs/release.pub > panda/certs/release.pub
git show 70b17dd8:panda/crypto/sign.py > panda/crypto/sign.py
git show 70b17dd8:panda/crypto/rsa.c > panda/crypto/rsa.c
git show 70b17dd8:panda/crypto/rsa.h > panda/crypto/rsa.h
git show 70b17dd8:panda/crypto/sha.c > panda/crypto/sha.c
git show 70b17dd8:panda/crypto/sha.h > panda/crypto/sha.h
git show 70b17dd8:panda/crypto/hash-internal.h > panda/crypto/hash-internal.h

chmod 600 panda/certs/debug  # protect the private key

# CRITICAL: add to .gitignore so the private key is NEVER committed
echo "panda/certs/debug" >> panda/.gitignore
echo "panda/certs/debug.pub" >> panda/.gitignore
```

### Phase 2.2 — Add a MADS safety param bit

**File: `opendbc_repo/opendbc/car/proton/values.py`**
```python
class ProtonSafetyFlags(IntFlag):
  STOCK_ACC = 1
  IGNORE_IGNITION_LINE = 2
  MADS_LATERAL_STANDBY = 4   # NEW — keep controls_allowed in cruise standby
```

**File: `opendbc_repo/opendbc/safety/modes/proton.h`** — add the constant:
```c
#define PROTON_SAFETY_PARAM_STOCK_ACC 1U
#define PROTON_SAFETY_PARAM_IGNORE_IGNITION_LINE 2U
#define PROTON_SAFETY_PARAM_MADS_LATERAL_STANDBY 4U   // NEW
```

### Phase 2.3 — The firmware edit: keep controls_allowed in standby

**File: `opendbc_repo/opendbc/safety/modes/proton.h`**

Add a global flag, set it in `proton_init`, and use it to bypass `pcm_cruise_check`:
```c
// Top of file (with other statics)
static bool proton_mads_lateral_standby = false;

// In proton_init (line ~163):
static safety_config proton_init(uint16_t param) {
  ignore_ignition_line = ((param & PROTON_SAFETY_PARAM_IGNORE_IGNITION_LINE) != 0U);
  proton_mads_lateral_standby = ((param & PROTON_SAFETY_PARAM_MADS_LATERAL_STANDBY) != 0U);  // NEW
  // ... rest unchanged ...
}

// In proton_rx_hook, the ACC_CMD handling (line ~77):
if ((bus == 2) && (addr == (int)PROTON_ACC_CMD)) {
  bool stock_engaged = GET_BIT(msg, 36U) || GET_BIT(msg, 38U);
  // ... debounce counter logic unchanged ...

  bool pcm_engaged = stock_engaged || (saw_stock_engaged && off_count < 5);

  // MADS: when enabled, never clear controls_allowed from stock ACC dropping.
  // This keeps the heartbeat mismatch check happy during standby steering.
  // Note: controls_allowed does NOT gate steering torque on Proton (tx_hook
  // checks LKAS bit consistency + torque cap, not controls_allowed).
  if (proton_mads_lateral_standby) {
    if (pcm_engaged && !proton_pcm_cruise_engaged_prev) {
      controls_allowed = true;
    }
    // DO NOT clear controls_allowed when pcm_engaged goes false
  } else {
    pcm_cruise_check(pcm_engaged);  // original behavior
  }
  proton_pcm_cruise_engaged_prev = pcm_engaged;
}
```

**What this changes:** When MADS is enabled (safety param bit set), the firmware keeps
`controls_allowed = true` once it's been set, even when stock ACC disengages. This is
exactly what KA1's firmware did unconditionally. The `controlsMismatch` check in
`selfdrived.py` then never fires.

**What this does NOT change:**
- Steering torque still gated by LKAS bit consistency + torque cap (≤599)
- Relay malfunction check unchanged
- CAN lag / heartbeat timeout still disengage
- All `IMMEDIATE_DISABLE` events still fire from openpilot
- The `controls_allowed` flag only feeds the heartbeat mismatch check — it does NOT
  gate any actuation on Proton

### Phase 2.4 — Set the safety param when MADS is on

**File: `opendbc_repo/opendbc/car/proton/interface.py`** — currently:
```python
safety_param = ProtonSafetyFlags(0)
if Features().has("stock-acc"):
    safety_param |= ProtonSafetyFlags.STOCK_ACC
if candidate == CAR.PROTON_X50:
    safety_param |= ProtonSafetyFlags.IGNORE_IGNITION_LINE
ret.safetyConfigs[0].safetyParam = int(safety_param)
```

Add MADS flag (read from the same toggle file):
```python
safety_param = ProtonSafetyFlags(0)
if Features().has("stock-acc"):
    safety_param |= ProtonSafetyFlags.STOCK_ACC
if candidate == CAR.PROTON_X50:
    safety_param |= ProtonSafetyFlags.IGNORE_IGNITION_LINE
# MADS: set firmware flag so controls_allowed survives cruise standby
try:
    with open("/data/params/MadsEnabled") as _f:
        if _f.read().strip() == "1":
            safety_param |= ProtonSafetyFlags.MADS_LATERAL_STANDBY
except (FileNotFoundError, OSError):
    pass
ret.safetyConfigs[0].safetyParam = int(safety_param)
```

**Note:** `safetyParam` is read at CarParams initialization (device boot / pandad
startup). Toggling MADS on/off requires a reboot for the firmware flag to take effect.
This is acceptable (matches the toggle UX — user enables in app, reboots device).

### Phase 2.5 — Build + sign the firmware

```bash
cd /Users/lutfimacmini/Documents/Project/bukapilot/panda

# Install build deps (arm-none-eabi toolchain)
# NOTE: This must be done on a Linux machine (or Docker) — the ARM toolchain
# doesn't run natively on macOS. Use the same Docker/Colima setup as the model
# conversion, or build on the KA2 device itself (it's aarch64 Linux).
scons panda_h7.bin.signed   # auto-signs with panda/certs/debug
```

The build produces `board/obj/panda_h7.bin.signed` (debug-signed, ~81KB). The SConscript
auto-adds `-DALLOW_DEBUG` and signs with `./certs/debug`. No separate sign step needed.

### Phase 2.6 — Deploy to device

**On the KA2 device** (via SSH):
```bash
# Back up the current working binary FIRST
cp /data/openpilot/panda/board/obj/panda_h7.bin.signed /data/panda_backup.bin.signed

# Push the new binary
scp panda_h7.bin.signed kommu@<device>:/data/openpilot/panda/board/obj/

# Reboot — pandad will detect signature mismatch and auto-flash
ssh kommu@<device> "sudo reboot"
```

On reboot, `pandad.py:flash_panda()` compares the expected signature (from the new
`panda_h7.bin.signed`) against the running panda's signature. Mismatch → `panda.flash()`
pushes the new binary via the bootstub soft-loader. The bootstub validates the debug
signature, accepts it, and the new firmware runs.

**If the flash fails or firmware is bad:** See [Revert procedure](#revert).

---

<a id="phase-3"></a>
## Phase 3: Testing & validation

### 3.1 — Pre-flight (before driving)
1. **Verify toggle is OFF** — device boots normally, 0.10 model works, no behavior change
2. **SSH check controlsAllowed:** `cansdump` or log inspection — in standby with MADS off,
   `pandaStates[].controlsAllowed` should be False (stock behavior preserved)
3. **Enable MADS toggle, reboot** — verify `safetyParam` includes the MADS bit:
   ```python
   # On device, check CarParams
   python3 -c "from cereal import car; import cereal.messaging as m; \
     p = m.recv_one(m.sub_sock('carParams')); print(p.carParams.safetyConfigs[0].safetyParam)"
   # Should show 4 (MADS bit) or 6 (MADS + IGNORE_IGNITION for X50)
   ```

### 3.2 — Static standby test (car parked, engine on)
1. Arm cruise MAIN — verify openpilot engages lateral (green steering indicator)
2. Press gas pedal — verify lateral STAYS engaged, speed increases (manual)
3. Release gas — verify lateral still engaged (car coasts)
4. Press brake — verify FULL disengage (this is the user's explicit choice)
5. Re-arm MAIN — verify lateral re-engages

### 3.3 — `controlsAllowed` persistence test
Monitor `pandaStates` while in standby for >5 seconds:
```bash
# On device, stream pandaStates and check controlsAllowed
python3 -c "
import cereal.messaging as m
s = m.sub_sock('pandaStates')
while True:
  m = s.receive()
  for ps in m.pandaStates:
    if not ps.controlsAllowed:
      print('CONTROLS_NOT_ALLOWED — MADS firmware flag not working!'); break
"
```
If `controlsAllowed` drops after 100ms in standby, the firmware edit didn't take effect.

### 3.4 — Driving test (low-speed, empty road)
1. Find an empty straight road, ~30 km/h
2. Arm MAIN — verify steering engages
3. Modulate gas manually — verify steering stays on, speed follows your foot
4. Test for 5+ minutes — verify no unexpected disengagements
5. Press brake — verify clean disengage
6. Test SET button — verify normal ACC still works (steering + speed control)

### 3.5 — Fail-safe verification
Verify these STILL disengage under MADS:
- Door open → IMMEDIATE_DISABLE ✓
- Seatbelt unlatched → IMMEDIATE_DISABLE ✓
- Steering wheel override → lateral suspends ✓
- Steer fault → IMMEDIATE_DISABLE ✓
- CAN error → IMMEDIATE_DISABLE ✓
- Brake pedal → full disengage ✓

---

<a id="revert"></a>
## Revert / recovery procedure

### If Python changes cause issues:
```bash
# On device
cd /data/openpilot
git checkout HEAD -- selfdrive/appbridged/appbridged.py \
  opendbc_repo/opendbc/car/proton/carstate.py \
  opendbc_repo/opendbc/car/proton/carcontroller.py \
  selfdrive/selfdrived/selfdrived.py
# Remove the toggle file
rm /data/params/MadsEnabled
sudo reboot
```

### If firmware causes boot-loop / OP won't engage:
```bash
# SSH in (device may be slow due to boot-loop, be patient)
# Restore the backup
cp /data/panda_backup.bin.signed /data/openpilot/panda/board/obj/panda_h7.bin.signed
# Or restore from git (if the new binary was committed):
cd /data/openpilot
git checkout HEAD~1 -- panda/board/obj/panda_h7.bin.signed
# Reboot — pandad will reflash the known-good binary
sudo reboot
```

**The recovery works because:** pandad runs on every boot, compares firmware signatures,
and reflashes automatically. Even in a boot-loop, pandad keeps trying. Restoring the old
binary + reboot = pandad flashes the good one.

---

<a id="never-touch"></a>
## What we will NEVER touch (the bootstub)

**`panda/board/obj/bootstub.panda_h7.bin`** — DO NOT modify, rebuild, or reflash this.

The bootstub:
- Lives in flash sector 0 (separate from the app)
- Validates the app's RSA signature before loading it
- Is the ONLY recovery path if the app is bad
- If corrupted → **permanent brick** (KA2 has no DFU GPIO control)

Our changes only ever touch:
- `panda_h7.bin.signed` (the app) — recoverable
- `proton.h` (app safety code) — compiled into the app, recoverable
- Python files — trivially recoverable

---

<a id="file-reference"></a>
## Full file reference

### Files to modify (Python)
| File | Change |
|---|---|
| `selfdrive/appbridged/appbridged.py` | Add MADS toggle (file-based, `/data/params/MadsEnabled`) |
| `opendbc_repo/opendbc/car/proton/carstate.py` | Add `lat_only` latch + `mads_enabled` flag |
| `selfdrive/selfdrived/selfdrived.py` | Gate gas-pedal disengage on `not mads_on` |
| `opendbc_repo/opendbc/car/proton/carcontroller.py` | Suppress longitudinal when `CS.lat_only` |
| `opendbc_repo/opendbc/car/proton/interface.py` | Set `MADS_LATERAL_STANDBY` safetyParam bit |
| `opendbc_repo/opendbc/car/proton/values.py` | Add `MADS_LATERAL_STANDBY = 4` to ProtonSafetyFlags |
| KommuDrive iOS: `SettingsSheet.swift` | Add MADS toggle row |
| KommuDrive iOS: `DeviceSettings.swift` | Add `madsEnabled` field |

### Files to modify (firmware)
| File | Change |
|---|---|
| `opendbc_repo/opendbc/safety/modes/proton.h` | Add `proton_mads_lateral_standby` global; bypass `pcm_cruise_check` when set |
| `panda/board/obj/panda_h7.bin.signed` | Rebuilt + re-signed (the output of the build) |

### Files to recover (signing keys — from commit `70b17dd8`)
| File | Purpose | Commit to repo? |
|---|---|---|
| `panda/certs/debug` | RSA private key | **NO** — `.gitignore` it, keep local only |
| `panda/certs/debug.pub` | Debug public key | NO (already embedded in cert.h) |
| `panda/certs/release.pub` | Release public key | NO (already embedded in cert.h) |
| `panda/crypto/sign.py` | Signing script | YES (needed for build) |
| `panda/crypto/rsa.c`, `rsa.h`, `sha.c`, `sha.h`, `hash-internal.h` | Crypto C files | YES (needed for bootstub build — but we don't rebuild bootstub) |

### Files NEVER to touch
| File | Reason |
|---|---|
| `panda/board/obj/bootstub.panda_h7.bin` | Bootstub — brick risk, no recovery |
| `panda/board/bootstub.c` | Bootstub source — same |

---

## Key technical findings (for future reference)

### How Proton engages today (KA2, no MADS)
1. `pcmCruise = True` (inherited from `CarInterfaceBase.get_std_params`, interfaces.py:207)
2. Driver presses SET → CAN `ACC_CMD.ACC_REQ` goes high (bus 2)
3. `carstate.py:199-201` computes `cruiseState.enabled = (ACC_REQ + STANDSTILL_REQ + ACCEL_ALLOWED) > 1`
4. `car_specific.py:181-183` fires `EventName.pcmEnable` on rising edge
5. State machine → `enabled` → controlsd computes `latActive = True`

### How standby differs
- In standby (MAIN armed, no SET): `ACC_REQ = 0` → `cruiseState.enabled = False` → no `pcmEnable` → OP stays disabled
- MADS keeps `cruiseState.enabled = True` via the latch → OP stays engaged for lateral

### The `controlsMismatch` check (selfdrived.py:497-502)
```python
if self.enabled and any(not ps.controlsAllowed for ps in self.sm['pandaStates']
       if ps.safetyModel not in IGNORED_SAFETY_MODES):
    self.mismatch_counter += 1
```
This is the wall. The firmware fix (Phase 2.3) keeps `controlsAllowed = True` so this
counter never increments.

### Why the abandoned `proton_mads` branch failed on KA2
The branch (commit `27742b7b3`) was written for KA1's permissive firmware. When ported
to KA2, the latch worked for ~100ms then got killed by `controlsMismatch`. The dev
didn't realize the firmware difference between KA1 and KA2 was the root cause.

### Safety integrity under MADS
These remain fully active (unchanged by MADS):
- **Firmware:** LKAS bit consistency, torque cap (≤599), zero-torque-when-inactive, relay malfunction check
- **Firmware:** CAN lag disengage, heartbeat timeout
- **openpilot:** All `IMMEDIATE_DISABLE` events (steer fault, CAN error, door open, seatbelt, wrong gear, etc.)
- **openpilot:** Driver steering override (suspends lateral, resumes on release)
- **Driver:** Brake pedal always disengages (user's explicit choice)

The only thing MADS removes is: **gas pedal as a lateral disengage trigger.** This is
the intended behavior — the driver is intentionally using the gas manually.

### CAN addresses (Proton)
| Addr | Name | Role |
|---|---|---|
| **432** (`0x1B0`) | `ADAS_LKAS` | Steering torque / LKAS. Carries `STEER_CMD`, `LKAS_ENGAGED1`, `LKAS_LINE_ACTIVE`. Bus 0. |
| **417** (`0x1A1`) | `ACC_CMD` | ACC command. `ACC_REQ` (bit 36), `STANDSTILL_REQ` (bit 38), `ACCEL_ALLOWED` (bit 37), `CRUISE_DISABLED` (bit 12). Bus 0 + bus 2 (camera). |
| **643** (`0x283`) | `ACC_BUTTONS` | Cruise buttons. `SET_BUTTON` (bit 4), `RES_BUTTON` (bit 3), `CRUISE_BTN`/cancel (bit 15). Bus 0. |
| **419** (`0x1A3`) | `PCM_BUTTONS` | `ICC_ON` (bit 20), `CRUISE_AVAILABLE` (bit 17), `ACC_SET_SPEED`. Camera bus 2. |
