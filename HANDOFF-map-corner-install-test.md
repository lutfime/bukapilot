# HANDOFF — Map Corner Slowdown: Install & Test on KA2 Device

**Branch:** `x70-map` (commit `e2dd6bae2`, pushed to `origin/x70-map`)
**Feature:** Map-aware corner slowdown for Proton X70, slowdown-only.
**Source:** Ported from KisaPilot/sunnypilot mapd (pfeiferj original, MIT).
**Status:** Code complete, builds clean (iOS app + Python syntax). NOT yet tested on device.

---

## What this feature does (one paragraph)

A new process `mapd_kommu` runs on the KA2. It reads GPS (`gpsLocation`, 1 Hz from qcomgpsd),
fetches road geometry from OpenStreetMap (public Overpass API, multi-endpoint fallback),
matches the car's position + heading to the correct road, computes upcoming curvature via
spline interpolation, and publishes an advisory corner speed
(`v = sqrt(budget / kappa)`) to Params. The longitudinal planner clamps `v_cruise` down to
this value before the MPC — so the MPC plans a smooth decel to the lower target. It is
**slowdown-only by `min()` construction**: it can never accelerate the car. When GPS is lost,
OSM fetch fails, or the route can't be matched, `MapCornerValid` goes false and the planner
ignores it entirely (identical to today's behavior). Master enable defaults **off**.

---

## Files changed (all in commit `e2dd6bae2`)

**Device (Python) — the actual fix:**
| File | Change |
|---|---|
| `selfdrive/mapd_kommu/` (NEW, 10 files) | The mapd process: `mapd.py`, `config.py`, `lib/NodesData.py` (curvature math), `lib/Route.py` (map matching), `lib/WayCollection.py`, `lib/WayRelation.py`, `lib/WayRelationIndex.py`, `lib/geo.py`, `lib/osm.py` (Overpass client), `lib/default_speeds.json` |
| `selfdrive/controls/lib/longitudinal_planner.py:144` | The slowdown clamp: `v_cruise = min(v_cruise, v_map)` (16 lines) |
| `system/manager/process_config.py:101-103` | Registers `mapd_kommu` as KA2-only, on-road process |
| `common/params_keys.h:46-51` | 5 new param keys |
| `pyproject.toml:79-82` | Added `overpy` + `scipy` deps |
| `tools-local/test_map_corner.py` (NEW) | Offline validator |

**iOS app (Swift) — tuning UI:**
| File | Change |
|---|---|
| `kommu-drive/.../DeviceService.swift:463-531` | `fetchMapCornerParams()` + `saveMapCornerParam()` (SSH → params get/put via base64-encoded Python) |
| `kommu-drive/.../TuningViewModel.swift` | Map corner state + fetch/save methods |
| `kommu-drive/.../TuningSheet.swift` | "Map Corner Slowdown" UI section (enable toggle, aggressiveness slider, lookahead, live status) |

**Design doc:**
| File | Content |
|---|---|
| `selfdrive/mapd_kommu/NOTES.md` | Full design rationale, adaptations, offline-upgrade path |

---

## Device access (how to reach the KA2)

- **SSH:** `ssh kommu@<device-ip>` port 22, ed25519 key auth (the key is embedded in the iOS app's `DeviceService.swift:9-26`; if SSHing from a Mac, use the same key or set up your own).
- **Device IP:** either the hotspot IP (`settings.hotspotIp` in the app, typically `192.168.43.1` when on the device's "kommu" hotspot) or the LAN IP (`settings.localIP`).
- **Repo path on device:** `/data/openpilot` (this is the openpilot fork the KA2 runs).
- **Python on device:** `/usr/local/venv/bin/python3` — always use this, not system python.
- **Params CLI access on device:** there is no standalone `params` binary; use Python:
  ```bash
  /usr/local/venv/bin/python3 -c "import sys; sys.path.insert(0,'/data/openpilot'); from openpilot.common.params import Params; p=Params(); print(p.get('MapCornerEnabled'))"
  ```

---

## INSTALL STEPS (do these in order)

### Step 1 — Get the code onto the device

The fork runs from `/data/openpilot`. The new code must land there. Options:

**Option A (if the device pulls from git):**
```bash
ssh kommu@<ip> "cd /data/openpilot && git fetch origin && git checkout x70-map && git pull origin x70-map"
```

**Option B (if git fetch isn't set up on device, rsync from Mac):**
```bash
# From the Mac, in the bukapilot repo root:
rsync -avz --exclude='.git' --exclude='DerivedData' --exclude='kommu-drive' \
  selfdrive/mapd_kommu/ kommu@<ip>:/data/openpilot/selfdrive/mapd_kommu/
# Also update the 3 edited files:
scp selfdrive/controls/lib/longitudinal_planner.py kommu@<ip>:/data/openpilot/selfdrive/controls/lib/
scp system/manager/process_config.py kommu@<ip>:/data/openpilot/system/manager/
scp common/params_keys.h kommu@<ip>:/data/openpilot/common/
```

**IMPORTANT — the overlay twin:** openpilot on the KA2 reads from `/data/safe_staging/merged/` (upper overlay) AND `/data/openpilot/` (lower). The existing PID tuning code mirrors edits to both (see `DeviceService.swift:395-396`). For `mapd_kommu` (a NEW process, not an edit to an existing file), it only needs to be in `/data/openpilot/`. But `longitudinal_planner.py`, `process_config.py`, and `params_keys.h` are edits to existing files — they may need mirroring to `/data/safe_staging/merged/` too:
```bash
ssh kommu@<ip> "cp /data/openpilot/selfdrive/controls/lib/longitudinal_planner.py /data/safe_staging/merged/selfdrive/controls/lib/ 2>/dev/null; cp /data/openpilot/system/manager/process_config.py /data/safe_staging/merged/system/manager/ 2>/dev/null; cp /data/openpilot/common/params_keys.h /data/safe_staging/merged/common/ 2>/dev/null; echo done"
```
(If `/data/safe_staging/merged/` doesn't have those subdirs, the `cp` will fail silently — that's fine, it means the device reads from `/data/openpilot/` directly.)

### Step 2 — Install Python dependencies

`overpy` and `scipy` are new deps. Install into the device venv:
```bash
ssh kommu@<ip> "/usr/local/venv/bin/pip install overpy scipy"
```
Verify:
```bash
ssh kommu@<ip> "/usr/local/venv/bin/python3 -c 'import overpy, scipy; print(\"overpy\", overpy.__version__, \"scipy\", scipy.__version__)'"
```

### Step 3 — Rebuild `params_pyx.so` (the C++ param whitelist)

**Why this is needed:** the params key whitelist (`MapCornerEnabled`, etc.) is compiled INTO `common/params_pyx.so` at build time. `params_keys.h` is `#include`d by `params.cc:11` and baked into a C++ `unordered_map`. The repo ships a prebuilt `params_pyx.so` (ARM aarch64) — dropping our edited `.h` on the device does NOTHING until the `.so` is recompiled from it. At runtime, `checkKey()` throws `UnknownKeyName` for any key not in the compiled map.

**Safety:** rebuilding is NOT dangerous to the car — params only gates which config keys are accepted; it cannot affect braking/steering. The only risk is a broken `.so` preventing openpilot from starting. Mitigate by **backing up the original `.so` first** (one command, instant rollback).

**No AGNOS reflash needed.** This is userspace only, no OS/kernel/firmware touched. Fully reversible by swapping the `.so` back.

**Do it safely — backup, scoped rebuild, verify:**

```bash
# 1. BACK UP the working .so (instant rollback insurance)
ssh kommu@<ip> "cp /data/openpilot/common/params_pyx.so /data/openpilot/common/params_pyx.so.stock"

# 2. Remove the prebuilt marker so scons is allowed to rebuild
ssh kommu@<ip> "rm -f /data/openpilot/prebuilt"

# 3. Rebuild ONLY params_pyx.so (scoped target — NOT the whole project)
#    This compiles params_keys.h → params.cc → params_pyx.so. Links against _common, zmq, json11.
ssh kommu@<ip> "cd /data/openpilot && scons -j4 common/params_pyx.so"

# 4. Verify the build succeeded (no errors above) and the .so is still aarch64
ssh kommu@<ip> "file /data/openpilot/common/params_pyx.so"

# 5. Verify the new keys are accepted
ssh kommu@<ip> "/usr/local/venv/bin/python3 -c \"import sys; sys.path.insert(0,'/data/openpilot'); from openpilot.common.params import Params; Params().put('MapCornerEnabled','1'); print('OK' if Params().get('MapCornerEnabled')=='1' else 'FAIL')\""
```
If step 5 prints `OK`, the rebuild worked. If it throws `UnknownKeyName`, the rebuild didn't pick up the new `.h` — investigate before continuing.

**ROLLBACK (if openpilot won't start after rebuild):**
```bash
ssh kommu@<ip> "cp /data/openpilot/common/params_pyx.so.stock /data/openpilot/common/params_pyx.so && touch /data/openpilot/prebuilt"
# then reboot or restart manager
```
This restores the known-good `.so` and openpilot boots normally.

**Notes:**
- Scope the scons target to `common/params_pyx.so` — do NOT run bare `scons` (which rebuilds the whole project: UI, modeld, everything — slow and risks unrelated errors).
- If `scons` isn't on the device (prebuilt releases sometimes omit the toolchain), the alternative is to cross-build the `.so` in an aarch64 Linux Docker container on your Mac (the `MODEL-CONVERSION-GUIDE.md` has this pattern) and `scp` it over. The backup-before-overwrite step still applies.
- Rebuilding does NOT require re-signing, re-flashing, or a reboot to take effect — just a manager restart (Step 4) so processes pick up the new `.so`.

### Step 4 — Restart openpilot so the new process registers

`process_config.py` change requires a manager restart:
```bash
ssh kommu@<ip> "pkill -f manager; sleep 2; cd /data/openpilot && ./launch_openpilot.sh &"
# OR simply reboot:
ssh kommu@<ip> "sudo reboot"
```
After restart, verify `mapd_kommu` is in the process list:
```bash
ssh kommu@<ip> "ps aux | grep mapd_kommu | grep -v grep"
```
It should appear when the car is on-road (the `only_onroad` gate). If off-road, it won't start until you go on-road.

---

## TEST PLAN (do these in order, stop on first failure)

### Test 1 — Offline math validation (no car needed)

From the Mac repo root (this validates the ported math, not the device):
```bash
cd /Users/lutfimacmini/Documents/Project/bukapilot
PYTHONPATH=. python3 tools-local/test_map_corner.py --lat 3.1390 --lon 101.6869 --bearing 90 --budget 2.5 -v
```
**Expected:** connects to an Overpass endpoint, fetches ways, attempts route match. May return "no corner / not located" if the bearing doesn't match a road at that exact point — that's OK. The goal is to confirm no Python import errors and the pipeline runs end-to-end.

If Overpass endpoints are rate-limited (we saw this a lot during development — 406 / "Server load too high"), that's expected flakiness, not a bug. Retry later or try a different lat/lon.

### Test 2 — Process startup on device (car parked, on-road)

1. Turn the car ON (so openpilot enters on-road state).
2. SSH in and check the process is alive:
   ```bash
   ssh kommu@<ip> "ps aux | grep mapd_kommu | grep -v grep"
   ```
3. Check the logs for errors:
   ```bash
   ssh kommu@<ip> "tail -50 /data/log/ mapd_kommu* 2>/dev/null || grep -i mapd_kommu /data/log/system.log | tail -20"
   ```
   **Expected:** no Python tracebacks. If `overpy`/`scipy` import fails, re-do Step 2. If "UnknownKeyName" appears, re-do Step 3.

### Test 3 — Params round-trip via SSH (car parked)

Manually verify the planner can read what mapd writes:
```bash
# Enable the feature
ssh kommu@<ip> "/usr/local/venv/bin/python3 -c \"import sys; sys.path.insert(0,'/data/openpilot'); from openpilot.common.params import Params; p=Params(); p.put_bool('MapCornerEnabled', True); p.put('MapCornerBudget','2.5'); print('enabled=', p.get_bool('MapCornerEnabled')); print('budget=', p.get('MapCornerBudget'))\""
```
**Expected:** `enabled= True`, `budget= 2.5`.

Then check what mapd is publishing (after GPS has a fix — may take 30-60s outdoors):
```bash
ssh kommu@<ip> "/usr/local/venv/bin/python3 -c \"import sys; sys.path.insert(0,'/data/openpilot'); from openpilot.common.params import Params; p=Params(); print('valid=', p.get_bool('MapCornerValid')); print('vCorner=', p.get('vCruiseMapCorner'), 'm/s')\""
```
**Expected:** once GPS locks, `valid= True` and `vCorner` is a sensible m/s value (e.g. `12.5` = 45 km/h). If `valid= False` persistently, the GPS may not have a fix (check `gpsLocation.hasFix`) or map matching is failing (check logs for "Route NOT located").

### Test 4 — Dry-run logging (car parked or short drive, feature DISABLED)

Before enabling on a real drive, log what mapd *would* do without affecting the car. Keep `MapCornerEnabled = false` (the clamp is a no-op when disabled). Drive a route with corners, then check the published advisory:
```bash
# After a short drive with corners:
ssh kommu@<ip> "/usr/local/venv/bin/python3 -c \"import sys; sys.path.insert(0,'/data/openpilot'); from openpilot.common.params import Params; p=Params(); print('valid=', p.get_bool('MapCornerValid')); print('vCorner=', p.get('vCruiseMapCorner'))\""
```
Optionally add CSV logging to `mapd.py publish_to_params()` temporarily to record v_corner over time for analysis.

### Test 5 — Live test (LOW TRAFFIC, known corner)

Only after Tests 1-4 pass:
1. Set `MapCornerEnabled = true`, `MapCornerBudget = 2.5` via the iOS Tuning UI or SSH.
2. Drive a familiar road with a known corner at a quiet time.
3. **Observe:** the car should scrub speed *before* the corner (the MPC decels to the advisory). If it doesn't slow, or slows too late/too early, adjust `MapCornerBudget` (lower = slower, e.g. 2.0) in the iOS slider.
4. **Safety:** keep `MapCornerEnabled = false` by default. Only enable for test drives. The feature is slowdown-only so it cannot cause unintended acceleration, but bad OSM data could cause unnecessary braking — if it brakes oddly, disable immediately and check the `vCruiseMapCorner` value.

---

## TUNING PARAMETERS (what the iOS slider changes)

| Param | Default | Range | Effect |
|---|---|---|---|
| `MapCornerEnabled` | false | on/off | Master switch. Off = invisible to the planner. |
| `MapCornerBudget` | 2.5 | 1.0–4.0 | Lateral accel budget (m/s²). Lower = slower corners. 2.5 = balanced, 2.0 = conservative, 3.0 = spirited. |
| `MapCornerLookahead` | 200 | 100–400 | Lookahead distance (m). Informational; the horizon also auto-scales with speed (`gps_speed × 15s`). |

---

## KNOWN RISKS & FAILURE MODES

1. **Overpass rate-limiting** — public endpoints frequently return 406 / "Server load too high". The code has multi-endpoint fallback (`osm.py`), but if ALL endpoints are down, `MapCornerValid` goes false → planner ignores it → safe. This is expected intermittency, not a bug.

2. **GPS 1 Hz** — `gpsLocation` from qcomgpsd is only 1 Hz. Fine for advisory corner speed (corners don't appear/disappear in 1 second), but map matching may lag briefly. The code holds the last valid value for 10s (`MAP_VALIDITY_TIMEOUT_S`) before invalidating, smoothing 1 Hz gaps.

3. **GPS fix gating** — qcomgpsd sets `hasFix` from `verticalAccuracy != 500` (`qcomgpsd.py:360`). The code also requires `horizontalAccuracy < 25m` (`mapd.py update_gps`). If GPS never gets a fix (indoor parking, urban canyon), `MapCornerValid` stays false. This is correct behavior.

4. **OSM data accuracy** — great on mapped highways/trunk roads, sparse on new housing estates. If OSM has no road geometry for the area, `fetch_road_ways_around_location` returns 0 ways → no route → invalid → safe.

5. **Map matching** (hardest sub-problem) — the ported `Route.py`/`WayCollection.py` pick the correct road + travel direction from bearing + position. If it matches the wrong road (parallel roads, junctions), the advisory could be wrong. Watch for this in Test 4 (dry-run) before trusting Test 5 (live).

6. **scipy/overpy not in venv** — if `pip install` (Step 2) isn't done, `mapd_kommu` crashes on import. Check `ps aux` in Test 2 — if it's not running after 10s on-road, this is the likely cause.

7. **params_keys rebuild** — if Step 3 didn't take, all `Params().put`/`get` for the new keys throw `UnknownKeyName`. The symptom is `mapd_kommu` crashing in `publish_to_params`.

---

## HOW TO DISABLE / REVERT

- **Quick disable (keep code):** `params put MapCornerEnabled 0` — the clamp becomes a no-op, identical to stock behavior.
- **Full revert:** `git checkout x70-test -- selfdrive/controls/lib/longitudinal_planner.py system/manager/process_config.py common/params_keys.h pyproject.toml && rm -rf selfdrive/mapd_kommu/ tools-local/test_map_corner.py` then redeploy.

---

## KEY SOURCE REFERENCES (for debugging)

- **Clamp site:** `selfdrive/controls/lib/longitudinal_planner.py:144-158` — the `min()` slowdown.
- **mapd main loop:** `selfdrive/mapd_kommu/mapd.py` — `MapDKommu` class, `publish_to_params()`.
- **Curvature math:** `selfdrive/mapd_kommu/lib/NodesData.py:97-140` (`spline_curvature_calculations`) + `:173` (`speed_section`: `v = sqrt(MAX_LAT_ACC / kappa)`).
- **OSM client:** `selfdrive/mapd_kommu/lib/osm.py` — multi-endpoint fallback, the Overpass query.
- **GPS read:** `selfdrive/mapd_kommu/mapd.py:131-147` (`update_gps`) — reads `gpsLocation`, gates on `hasFix` + `horizontalAccuracy < 25`.
- **Process registration:** `system/manager/process_config.py:101-103`.
- **Params keys:** `common/params_keys.h:46-51`.
- **iOS UI:** `kommu-drive/.../TuningSheet.swift` `mapCornerSection`.

## VALIDATION ALREADY DONE (don't need to repeat)

- All 10 Python files pass `py_compile` syntax check.
- Curvature math validated on synthetic 80m-radius circle: measured curvature 0.01257 vs expected 0.01250 (0.6% error); computed speed within 6% of theoretical (conservative = safe).
- iOS app builds clean (`xcodebuild ... BUILD SUCCEEDED`) including the new DeviceService helpers + TuningSheet section.
- OSM client tested against real endpoints (kumi, osm.ch, official) — confirmed endpoint flakiness is real and the multi-fallback is necessary.

## QUESTIONS TO ANSWER DURING TESTING (report back)

1. Does `overpy` install cleanly into the KA2 venv? (Step 2)
2. Does the params_keys rebuild (Step 3) actually make the new keys accepted, or is there an additional Cython rebuild step?
3. Does `mapd_kommu` start cleanly on-road? (Test 2)
4. How long does GPS take to get a fix, and does `MapCornerValid` go true? (Test 3)
5. On a dry-run drive, are the `vCruiseMapCorner` values sensible for known corners? (Test 4)
6. When enabled live, does the car scrub speed before corners, and is the decel smooth? (Test 5)
