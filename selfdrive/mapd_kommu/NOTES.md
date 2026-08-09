# mapd_kommu — Notes & Decisions

Map-aware corner slowdown for the Kommu KA2 (Proton X70). Ported from KisaPilot/sunnypilot's
mapd (originally by pfeiferj, MIT licensed). Adapted for KA2 hardware and our 0.10.3 control stack.

## What it does

Reads GPS (`gpsLocation`, 1 Hz from qcomgpsd), fetches road geometry from OpenStreetMap,
matches the car's position to the correct road + travel direction, computes upcoming curvature
via spline interpolation, and publishes an advisory corner speed
(`v = sqrt(a_lat_budget / kappa)`) to Params. The longitudinal planner clamps `v_cruise`
down to this value — slowdown-only by `min()` construction.

## Files (ported from KisaPilot `selfdrive/mapd/lib/`)

- `mapd.py` — main process loop (adapted: gpsLocation, Params output, budget from params)
- `config.py` — tuning constants + Kommu defaults
- `lib/NodesData.py` — spline curvature → speed math (unchanged, the core value)
- `lib/Route.py` — route building + map matching (unchanged)
- `lib/WayCollection.py`, `WayRelation.py`, `WayRelationIndex.py` — OSM way graph (unchanged)
- `lib/geo.py` — lat/lon geometry helpers (unchanged)
- `lib/osm.py` — Overpass client (adapted: multi-endpoint fallback, cloudlog)
- `lib/default_speeds.json` — country speed-limit defaults (for the SLA feature we don't use)

## Key adaptations from the KisaPilot/sunnypilot original

### 1. GPS source: `gpsLocation` (not `gpsLocationExternal`)
KA2 runs `qcomgpsd` (Qualcomm modem GPS) which publishes `gpsLocation` at 1 Hz. It does NOT
produce `gpsLocationExternal` (that's ublox/TICI only). All GPS reads use `gpsLocation`.

### 2. IPC: Params (not a liveMapData message)
We publish `vCruiseMapCorner` (FLOAT m/s) + `MapCornerValid` (BOOL) to Params instead of a
custom messaging type. This avoids editing the capnp schema and rebuilding cereal. The
longitudinal planner reads these each cycle in `update()`. Trade-off: the value isn't in qlogs
for replay — acceptable for v1; can add capnp logging later if needed.

### 3. OSM: public Overpass API (not a local osm-3s server)
KisaPilot vendors a local OSM server (osm-3s + opspline + libgfortran, ~hundreds of MB) for
offline use. We query public Overpass endpoints directly with multi-endpoint fallback. This
keeps the install light but requires internet (KA2 LTE is confirmed working).

### 4. Budget: runtime-tunable via Params
KisaPilot hardcodes `_MAX_LAT_ACC = 2.3` in NodesData.py. We read `MapCornerBudget` (default 2.5)
at runtime and rescale: `v_new = v_old * sqrt(budget / 2.3)`. This lets the iOS slider change
cornering aggressiveness live.

## Params keys

| Key | Type | Default | Meaning |
|---|---|---|---|
| `MapCornerEnabled` | BOOL | false | Master enable. Off = feature is invisible to the planner. |
| `MapCornerBudget` | FLOAT | 2.5 | Lateral accel budget (m/s²). Lower = slower corners. Range 1.0–4.0. |
| `MapCornerLookahead` | FLOAT | 200 | Lookahead distance (m). Currently informational; horizon also scales with speed. |
| `vCruiseMapCorner` | FLOAT | 0 | Advisory corner speed (m/s). Written by mapd_kommu, read by planner. |
| `MapCornerValid` | BOOL | false | Whether the advisory is currently usable (GPS fix + route matched + not timed out). |

## Offline upgrade path (local OSM server)

If you need offline operation (no LTE), port KisaPilot's local server approach:
1. Vendor `osm-3s_v0.7.57.tar.xz`, `opspline.tar.gz`, `libgfortran.tar.gz` (from KisaPilot
   `selfdrive/mapd/assets/`).
2. Install via `system/manager/local_osm_install.py` (port from KisaPilot).
3. Pre-download a region OSM extract (e.g. from geofabrik.de for Malaysia).
4. Change `osm.py` to query `localhost` osm3s instead of public endpoints.

This is ~hundreds of MB of install + region management. Not worth it for a single car with LTE.

## Dependencies

- `overpy` (Python Overpass client) — must be in the device venv. Add to requirements.
- `scipy` — used by NodesData for spline interpolation (`splprep`/`splev`). Already a dep
  of openpilot (used by MPC); verify present on KA2.
- `numpy` — already a core dep.

## Validation done

- All files syntax-checked (`py_compile`).
- Curvature math validated on a synthetic 80m-radius circle: measured curvature 0.01257 vs
  expected 0.01250 (0.6% error); speed within 6% of theoretical (conservative — safe).
- OSM client tested against real endpoints; multi-endpoint fallback confirmed necessary
  (public endpoints are frequently rate-limited/overloaded).

## NOT yet validated (needs device)

- Live GPS matching on real KA2 qcomgpsd output (1 Hz, hasFix gating).
- End-to-end on a real drive with corners.
- Latency of Params reads from the 20 Hz planner loop (should be fine — small file reads).
- `overpy` availability in the KA2 venv (may need `pip install overpy`).
