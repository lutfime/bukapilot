## openpilot (KA2) agent quickstart

This repo runs on a **Kommu KA2** (Rockchip RK3588). Hardware selection is **runtime-flagged** by presence of `/KA2` (see `openpilot/system/hardware/__init__.py`).

### What starts what (on this KA2 device)

- **systemd**
  - `kommu.service` → launches tmux session running `/usr/kommu/kommu.sh`
  - `kommu-usb-recovery.service` → USB gadget (RNDIS+ACM) + loader trigger via `/usr/kommu/kommu-usb-recovery.sh`
- **boot script chain**
  - `/usr/kommu/kommu.sh` → if `/data/continue.sh` exists, `exec`s it
  - `/data/continue.sh` → `cd /data/openpilot && ./launch_openpilot.sh`
  - `launch_openpilot.sh` → `launch_chffrplus.sh`
  - `launch_chffrplus.sh` → sets env, handles **KA2/AGNOS** update logic, then runs:
    - `system/manager/build.py` (if not `prebuilt`)
    - `system/manager/manager.py`

### The runtime “hub” (read these first)

- **Process supervisor**: `openpilot/system/manager/manager.py`
- **Process list + gating**: `openpilot/system/manager/process_config.py`
  - KA2-only processes include `system.hardware.ka2.status_led.indicatord`
- **Device state / onroad-offroad logic**: `openpilot/system/hardware/hardwared.py`
- **Device paths (logs/params)**: `openpilot/system/hardware/hw.h` (C++ Path helpers used widely)

### KA2-specific code (only)

- **KA2 hardware implementation**: `openpilot/system/hardware/ka2/hardware.py`
  - modem bring-up handled in KA2 modem code (`configure_modem`)
  - SD card: formatting support on `/dev/mmcblk1` (partitioned during format)
- **AGNOS updater for KA2**: `openpilot/system/hardware/ka2/agnos.py` + `agnos.json`
  - `launch_chffrplus.sh` picks KA2 manifest when `/KA2` exists
- **Status LED service**: `openpilot/system/hardware/ka2/status_led/indicatord.py`

### Repo top-level map (what’s where)

- **Core runtime**
  - `openpilot/` (python package; many “system/*” modules live here)
  - `system/` and `selfdrive/` (major subsystems; driving stack is mostly in `selfdrive/`)
- **IPC / schemas**: `cereal/` (capnp logs + messaging), `msgq_repo/` (msgq implementation; `msgq` symlink)
- **Vehicle interfaces**: `opendbc_repo/` (`opendbc` symlink)
- **Hardware safety / CAN**: `panda/`
- **Tools**: `tools/` (replay, plots, sim, etc.)
- **Build**: `SConstruct`, `site_scons/`, `compile_commands.json`

### On-device storage locations (KA2)

- **openpilot checkout**: `/data/openpilot`
- **params DB**: `/data/params` (also referenced by `Path::params()` in `openpilot/system/hardware/hw.h`)
- **logs / routes**: default `Path::log_root()` → `/data/media/0/realdata`
- **runtime logs**: `/data/log` (device-level); OS logs under `/var/log`
- **tmp**: `/data/tmp` (created by `/usr/kommu/kommu.sh`)
- **overlay/update staging**: `/data/safe_staging`, `/data/rootfs_overlay*`

### Fast “where is X handled?” pointers

- **Start/stop conditions**: `hardwared.py` publishes `deviceState.started`; `manager.py` uses it to gate processes.
- **Network/modem**: KA2 modem logic in `system/hardware/ka2/hardware.py`.
- **Adding a new daemon**: add to `process_config.py` and ensure gating function reflects KA2 needs.

### Lateral tuning workflow (drive → analyze → retune)

- **Analyzer**: `result/lateral_flm.py` — FLM-style lateral analyzer ported from StarPilot
  (MIT). Standalone (capnp+zstd+numpy only, no openpilot imports); reads loose
  `result/drives/*--<seg>.rlog.zst` files and writes one JSON report per route to
  `result/flm_reports/`.
  ```
  .venv/bin/python result/lateral_flm.py result/drives result/drives/Archive "result/drives/Archive 2"
  .venv/bin/python result/test_lateral_flm.py      # synthetic + real-data cross-validation
  ```
- **What it reports**: event counts/severity by speed band (understeer/oversteer,
  late/early turn-in, unwind too slow/fast, low-speed unwillingness, saturation,
  center chatter, curve oscillation) + PID diagnostics (p/i/f output split,
  utilization, angle-error RMS). Report includes the tuning snapshot active
  during the drive (`car.pid`).
- **Agent loop**: after a tuning change + drive, re-run and diff against the
  previous `result/flm_reports/*.json` — event counts/severity and i-fraction are
  the reward signal. Baseline (2026-08-16, 6 drives on pre-retune PID tuning):
  integrator-dominated output (i-fraction 0.5–0.7), dominant `late_turn_in`.
- **Sign convention**: curvature-family signals (desiredCurvature, camera-yaw
  actual_la) are sign-opposite to steering angle in this stack. Comparisons
  inside the analyzer are internally consistent; keep it in mind when comparing
  against steering-angle plots.
- Prefer this over the ad-hoc `result/pid_analyze*.py` / `wobble_*.py` /
  `corner_*.py` scripts for whole-drive lateral assessment; those remain useful
  for single-incident deep dives.

