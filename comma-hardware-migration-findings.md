# Comma Hardware Migration — Findings (USB-C / Harness / Safety)

> Compiled 2026-08-04. Investigates whether buying a comma four allows running
> v0.11 directly (skipping the NPU/RKNN conversion treadmill) and whether the
> existing Kommu car harness (USB-C) can be reused safely.

---

## TL;DR

- **Software:** Proton port is hardware-agnostic and transfers cleanly to comma
  hardware. Verified from code — zero KA2-specific CAN remapping.
- **Model:** On comma four, v0.11 runs natively (tinygrad-on-GPU). No conversion
  needed. This is the main win of buying comma.
- **USB-C cable:** Connector shape matches (both use USB-C / OBD-C). Pinout is
  NOT verified from code — must be tested physically before plugging into comma.
- **Real danger:** Wrong pinout CAN fry the comma's panda (12V shorted to data
  line). Comma redesigned hardware because of this exact failure in the field.

---

## 1. Software portability (VERIFIED from code)

### 1a. Proton car port is hardware-agnostic

The Proton port lives entirely in `opendbc_repo/opendbc/car/proton/` and uses
only stock opendbc APIs. No KA2-specific code in the data path:

- `interface.py` — uses `CarInterfaceBase`, `get_safety_config`, `PlatformConfig`
- `carstate.py` — reads CAN via `CANParser` on standard buses
- `values.py:21-23` — CANBUS layout:
  ```python
  main_bus = 0   # powertrain
  radar_bus = 1  # front radar
  cam_bus   = 2  # LKAS / ADAS camera
  ```

### 1b. CAN bus numbering identical to comma-shipped cars

There is a single global `Bus` enum (`opendbc_repo/opendbc/car/__init__.py:81`)
shared by ALL cars. The physical bus numbers are identical across every car:

| Car | main/pt bus | radar bus | cam bus |
|---|---|---|---|
| Toyota (comma) | 0 | 1 | 2 |
| Honda (comma) | 0 | 1 | 2 |
| Hyundai (comma) | 0 | 1 | 2 |
| **Proton (fork)** | 0 | 1 | 2 |

### 1c. ZERO KA2-specific CAN remapping

Searched for any fork-specific bus swapping/inverting/remapping.
**Found nothing.** No `if KA2: bus = swap(...)`. The KA2 runs the identical bus
numbering as comma. Bus number → physical CAN transceiver → physical pin, so if
KA2 used a different pinout, software remapping would be required. There is none.

**Strong inference:** Kommu copied comma's pinout. But not proof — physical
verification still needed (see §3).

---

## 2. Proton safety model (VERIFIED from code)

The `bukapilot-proton-findings.md` doc claimed Proton safety was a "permissive
no-op." That is OUTDATED. The actual implementation at
`opendbc_repo/opendbc/safety/modes/proton.h` is a real, substantive safety layer:

- `tx_hook` — blocks unsafe commands:
  - rejects LKAS when `steer_req ≠ line_active`
  - rejects nonzero steer when not requested
  - caps steer at `PROTON_MAX_STEER_SEEN = 599`
  - rejects conflicting ACC states
- `rx_hook` — monitors gas pedal, cruise buttons, stock engagement
- `fwd_hook` — blocks stock camera LKA/ACC during device transmit windows
- `check_relay = true` on LKAS/ACC messages — power relay must be engaged
- `PROTON_TX_MSGS[]` whitelist — only these 3 messages may be transmitted:
  - `PROTON_ADAS_LKAS` (0x1B0)
  - `PROTON_ACC_CMD` (0x1A1)
  - `PROTON_ACC_BUTTONS` (0x283)

**Implication:** If the firmware is correctly flashed, the firmware-level guard
is real. But a stock comma four won't have this — must flash Proton firmware.

---

## 3. USB-C cable verification (the open risk)

### 3a. The danger is real and documented

From [ophwug/docs](https://github.com/ophwug/docs) (community hardware reference):

> "The harness box outputs 12V over the OBD-C port immediately and is NOT a
> valid or compliant USB-PD port. It does not perform any power negotiation and
> supplies 12V directly immediately upon connection."

Comma staff (Adeeb) confirmed on Discord that bad cables have permanently fried
pandas: *"we removed [the debug connector] since the panda was getting fried on
3Xs with bad USB cables shorting VIN to the data lines."*

### 3b. How to verify — physical test required

**Reference pinout (comma official):**
- [commaai/hardware — OBD-C.sch.pdf](https://github.com/commaai/hardware/blob/master/harness/OBD-C.sch.pdf)

**Test procedure (multimeter + 24-pin breakout board):**
1. Unplug cable from both KA2 and car
2. Continuity mode on multimeter
3. At one end, probe VBUS pin; at other end probe each pin
4. **VBUS should ONLY beep to VBUS.** If VBUS beeps to any CAN/data pin →
   panda-killer, do not use.
5. Repeat for each CAN pair — should only beep to matching CAN pin.

**OBD-C mapping (USB-C pin → function):**
| USB-C pin | OBD-C function |
|---|---|
| VBUS (A4/A9/B4/B9) | 12V power |
| GND (A1/A12/B1/B12) | Ground |
| SBU1 (A8) | Ignition sense |
| SBU2 (B8) | Relay control |
| TX/RX pairs | CAN0/CAN1/CAN2 (H/L) |

### 3c. Comma panda has automatic orientation detection

`panda/board/drivers/harness.h:50-86` — the panda detects NORMAL vs FLIPPED
USB-C orientation via analog voltage on SBU1/SBU2 pins. This handles cable
reversibility but does NOT protect against a fully different pinout.

---

## 4. Fork-specific daemons that change on comma hardware

From `system/manager/process_config.py` — KA2-specific daemons that would NOT
carry over to comma four:

| Daemon | Purpose | On comma |
|---|---|---|
| `appbridged` | BLE + WiFi hotspot for Kommu phone app | Lost. Comma uses `athenad` (cloud/comma connect) — already in tree but commented out (`:68`) |
| `indicatord` | KA2 status LED | Lost; comma four has own LED + `soundd` |
| `ui` (mici) | Custom Qt/web UI | Comma runs native Qt UI; either use comma's or port mici |
| KA2 modem/SD/thermal | `system/hardware/ka2/hardware.py` (24KB) | Replaced by `tici/hardware.py` (already in-tree) |

The `/TICI` vs `/KA2` sentinel file (`system/hardware/__init__.py:9-20`) selects
the active hardware path. Switching is mostly changing which sentinel exists.

---

## 5. Cost / risk summary

| Risk | Damage target | Mitigation | Cost |
|---|---|---|---|
| Pinout mismatch | Comma panda (hardware fry) | Test cable before plugging in | $10-30 tester |
| Unflashed firmware | Car (unvalidated commands) | Flash Proton safety firmware | Time only |
| Bus crossing | Car (wrong ECU messages) | Same as pinout — correct harness | Same test |
| X70 placeholder tuning | Car (erratic steering) | Pre-existing — finish X70 tuning first | Time |

---

## 6. Recommended sequence

1. **Finish X70 tuning** first (it's a placeholder today per findings doc).
2. **Buy USB-C breakout board + use multimeter** ($10-30). Test cable.
3. **If pinout matches comma's PDF** → cable safe to reuse.
4. **If not** → re-terminate device-side USB-C (~$20) or buy comma OBD-C cable
   and splice to existing car-side wiring (~$30).
5. Then consider buying comma four. Flash Proton firmware as step zero.

---

## 7. Sources

- [ophwug/docs — Unofficial comma hardware documentation](https://github.com/ophwug/docs)
- [commaai/hardware — OBD-C.sch.pdf](https://github.com/commaai/hardware/blob/master/harness/OBD-C.sch.pdf)
- [Total Phase — USB-C continuity testing](https://www.totalphase.com/blog/2018/08/continuity-testing-usb-type-c-cables-using-advanced-cable-tester/)
- [Treedix 24-pin breakout board](https://www.amazon.com/Treedix-Female-Output-Breakout-Connector/dp/B09L816S5W)

## 8. Code references (verified)

- `system/hardware/__init__.py:9-20` — TICI/KA2/PC sentinel selection
- `system/hardware/ka2/hardware.py:645` — `has_internal_panda() = True`
- `selfdrive/pandad/spi.cc:32` — `SPI_DEVICE = "/dev/spidev0.0"` (internal panda)
- `panda/board/drivers/harness.h:50-86` — orientation auto-detect via SBU1/SBU2
- `opendbc_repo/opendbc/car/__init__.py:81` — global `Bus` enum
- `opendbc_repo/opendbc/car/proton/values.py:21-23` — CANBUS layout (0/1/2)
- `opendbc_repo/opendbc/safety/modes/proton.h` — full Proton safety impl
- `system/manager/process_config.py:68,73,88` — athenad commented, appbridged/indicatord KA2-only
