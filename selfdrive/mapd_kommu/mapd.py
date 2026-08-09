#!/usr/bin/env python3
"""
mapd_kommu — map-aware corner speed advisor for the Kommu KA2 (Proton X70).

Based on KisaPilot/sunnypilot's mapd (by pfeiferj, MIT licensed). Adapted for:
  - KA2 GPS: reads `gpsLocation` (qcomgpsd, 1 Hz) instead of `gpsLocationExternal`.
  - IPC: writes vCruiseMapCorner + MapCornerValid to Params (not a liveMapData message),
    so the existing 0.10.3 longitudinal planner can read it with no capnp change.
  - OSM: queries the public Overpass API directly via overpy (no local osm-3s server).
  - Slowdown-only: the planner applies v_cruise = min(v_cruise, v_map), so this process
    can only ever REDUCE speed, never increase it.

Publishes:
  - Params `vCruiseMapCorner` (FLOAT m/s): the curvature-derived advisory corner speed ahead.
  - Params `MapCornerValid` (BOOL): whether the advisory is currently usable.

When GPS is lost, OSM fetch fails, or the route can't be matched, MapCornerValid goes false
and the planner ignores the value entirely (identical to today's behavior).
"""
import threading
import time
from traceback import print_exception

import numpy as np

from openpilot.common.realtime import Ratekeeper, config_realtime_process
from openpilot.common.params import Params
from openpilot.common.swaglog import cloudlog

from openpilot.selfdrive.mapd_kommu.lib.osm import OSM
from openpilot.selfdrive.mapd_kommu.lib.geo import distance_to_points
from openpilot.selfdrive.mapd_kommu.lib.WayCollection import WayCollection
from openpilot.selfdrive.mapd_kommu.config import (
  QUERY_RADIUS,
  MIN_DISTANCE_FOR_NEW_QUERY,
  FULL_STOP_MAX_SPEED,
  LOOK_AHEAD_HORIZON_TIME,
  DEFAULT_LAT_ACCEL_BUDGET,
  MIN_V_CORNER,
  MAP_VALIDITY_TIMEOUT_S,
)


def excepthook(args):
  cloudlog.warning(f'mapd_kommu: threading exception:\n{args}')
  print_exception(args.exc_type, args.exc_value, args.exc_traceback)


threading.excepthook = excepthook


def _read_budget(params):
  """Read the lateral-accel budget from params, defaulting to DEFAULT_LAT_ACCEL_BUDGET."""
  raw = params.get("MapCornerBudget")
  try:
    val = float(raw) if raw else DEFAULT_LAT_ACCEL_BUDGET
    # Sanity clamp: plausible human cornering budgets are 1.0 (very gentle) to 4.0 (sporty).
    return float(np.clip(val, 1.0, 4.0))
  except (TypeError, ValueError):
    return DEFAULT_LAT_ACCEL_BUDGET


def _compute_corner_speed(route, budget, gps_speed):
  """
  Given a located route and a lateral-accel budget, compute the advisory corner speed (m/s)
  from the tightest curvature section currently active or just ahead.

  Uses route.current_curvature_speed_limit_section for the active section, which is computed
  by NodesData from the OSM road geometry as v = sqrt(MAX_LAT_ACC / kappa_peak).
  """
  if route is None or not route.located:
    return None

  horizon_m = max(gps_speed * LOOK_AHEAD_HORIZON_TIME, 50.0)
  sections = route.curvature_speed_limits_ahead
  ahead = route.next_curvature_speed_limit_sections(horizon_m)

  # The KisaPilot NodesData computes section speeds with a hardcoded _MAX_LAT_ACC.
  # Scale them to our runtime budget so the iOS slider actually changes behavior.
  # NodesData uses _MAX_LAT_ACC = 2.3; rescale: v_new = v_old * sqrt(budget / 2.3).
  scale = float(np.sqrt(budget / 2.3))

  candidates = []
  current = route.current_curvature_speed_limit_section
  if current is not None:
    candidates.append(current.value * scale)
  for s in ahead:
    candidates.append(s.value * scale)

  if len(candidates) == 0:
    return None

  v_corner = float(min(candidates))
  if v_corner < MIN_V_CORNER:
    # Don't let bad geometry force a near-stop; treat as invalid.
    return None
  return v_corner


class MapDKommu:
  def __init__(self):
    self.params = Params()
    self.osm = OSM()
    self.way_collection = None
    self.route = None
    self.last_gps_fix_timestamp = 0
    self.last_gps = None
    self.location_deg = None
    self.location_rad = None
    self.bearing_rad = None
    self.location_stdev = None
    self.gps_speed = 0.0
    self.last_fetch_location = None
    self.last_route_update_fix_timestamp = 0
    self._op_enabled = False
    self._disengaging = False
    self._query_thread = None
    self._lock = threading.RLock()
    # Last published values (so we can publish invalid on timeout)
    self._last_valid_t = 0.0

  def update_state(self, sm):
    sock = 'selfdriveState'
    if not sm.updated[sock] or not sm.valid[sock]:
      return
    sd = sm[sock]
    self._disengaging = not sd.enabled and self._op_enabled
    self._op_enabled = sd.enabled

  def update_gps(self, sm):
    # KA2 produces `gpsLocation` (qcomgpsd, 1 Hz), NOT gpsLocationExternal.
    sock = 'gpsLocation'
    if not sm.updated[sock] or not sm.valid[sock]:
      return

    log = sm[sock]

    # qcomgpsd sets hasFix from verticalAccuracy != 500. Use that plus an accuracy floor.
    if not log.hasFix:
      return
    if log.horizontalAccuracy > 25.0:
      # Too inaccurate to trust map matching. Don't update location; let validity time out.
      return

    self.last_gps = log
    self.last_gps_fix_timestamp = log.unixTimestampMillis if hasattr(log, 'unixTimestampMillis') else int(time.time() * 1000)
    self.location_rad = np.radians(np.array([log.latitude, log.longitude], dtype=float))
    self.location_deg = (log.latitude, log.longitude)
    self.bearing_rad = np.radians(float(log.bearingDeg))
    self.gps_speed = float(log.speed)
    self.location_stdev = float(log.horizontalAccuracy)

  def _query_osm_not_blocking(self):
    def query(osm, location_deg, location_rad, radius):
      cloudlog.info(f"mapd_kommu: OSM query at {location_deg} r={radius}m")
      lat, lon = location_deg
      try:
        ways = osm.fetch_road_ways_around_location(lat, lon, radius)
      except Exception as e:
        cloudlog.warning(f"mapd_kommu: OSM query failed: {e}")
        ways = []

      if len(ways) > 0:
        new_way_collection = WayCollection(ways, location_rad)
        with self._lock:
          self.way_collection = new_way_collection
          self.last_fetch_location = location_rad
          cloudlog.info(f"mapd_kommu: got {len(ways)} ways at {location_deg}")
      else:
        cloudlog.info("mapd_kommu: OSM query returned no ways (connectivity or empty area)")

    if self._query_thread is not None and self._query_thread.is_alive():
      return
    self._query_thread = threading.Thread(target=query,
                                          args=(self.osm, self.location_deg, self.location_rad, QUERY_RADIUS))
    self._query_thread.start()

  def updated_osm_data(self):
    if self.route is not None:
      distance_to_end = self.route.distance_to_end
      if distance_to_end is not None and distance_to_end >= MIN_DISTANCE_FOR_NEW_QUERY:
        return

    if self.location_rad is None:
      return

    if self.last_fetch_location is not None:
      distance_since_last = distance_to_points(self.last_fetch_location, np.array([self.location_rad]))[0]
      if distance_since_last < QUERY_RADIUS - MIN_DISTANCE_FOR_NEW_QUERY:
        return

    self._query_osm_not_blocking()

  def update_route(self):
    def update_proc():
      if self._disengaging:
        self.route = None
        return

      if self.way_collection is None or self.location_rad is None or self.bearing_rad is None:
        return

      if self.route is not None and self.last_route_update_fix_timestamp == self.last_gps_fix_timestamp:
        return

      self.last_route_update_fix_timestamp = self.last_gps_fix_timestamp

      if self.route is None or self.route.way_collection_id != self.way_collection.id:
        try:
          self.route = self.way_collection.get_route(self.location_rad, self.bearing_rad, self.location_stdev)
        except Exception:
          self.route = None
        return

      if self.gps_speed < FULL_STOP_MAX_SPEED:
        return

      self.route.update(self.location_rad, self.bearing_rad, self.location_stdev)
      if self.route.located:
        return

      try:
        self.route = self.way_collection.get_route(self.location_rad, self.bearing_rad, self.location_stdev)
      except Exception:
        self.route = None

    with self._lock:
      update_proc()

  def _write_status_file(self, now, v_corner, budget):
    """Write a plain-text status file for SSH debugging (`cat /tmp/mapd_kommu_status.txt`).
    Pure info — no params involved, so no params_keys rebuild needed."""
    # Diagnose why we're (in)valid, in priority order.
    if self.last_gps is None or self.last_gps_fix_timestamp == 0:
      status = "no_gps"           # process running, but qcomgpsd hasn't produced a usable fix yet
    elif self.location_stdev is not None and self.location_stdev > 25.0:
      status = f"gps_poor_acc ({self.location_stdev:.0f}m)"
    elif self.way_collection is None:
      status = "osm_fetching"     # haven't fetched any roads yet (first cycle, or all endpoints failed)
    elif self.last_fetch_location is None:
      status = "osm_empty"        # fetch ran but returned 0 ways
    elif self.route is None or not self.route.located:
      status = "no_route_match"   # got roads but none matched our position + bearing
    elif v_corner is None:
      status = "no_corner"        # on a road, but no curvature sections ahead (straight road)
    else:
      status = "active"

    road = ""
    sections = 0
    dist_to_corner = None
    try:
      if self.route is not None and self.route.located:
        road = self.route.current_road_name or "(unnamed)"
        secs = self.route.curvature_speed_limits_ahead
        sections = len(secs)
        if len(secs) > 0:
          dist_to_corner = secs[0].start
    except Exception:
      pass

    valid = (status == "active")
    lines = [
      f"status: {status}",
      f"valid: {valid}",
      f"v_corner_mps: {v_corner:.2f}" if v_corner is not None else "v_corner_mps: -",
      f"v_corner_kmh: {v_corner * 3.6:.1f}" if v_corner is not None else "v_corner_kmh: -",
      f"budget: {budget:.2f} m/s^2",
      f"road: {road}" if road else "road: -",
      f"sections_ahead: {sections}",
      f"dist_to_first_corner_m: {dist_to_corner:.0f}" if dist_to_corner is not None else "dist_to_first_corner_m: -",
      f"",
      f"# GPS",
      f"gps_fix_ts: {self.last_gps_fix_timestamp}",
      f"location: {self.location_deg}" if self.location_deg else "location: -",
      f"bearing_deg: {float(self.bearing_rad) * 180.0 / 3.14159:.0f}" if self.bearing_rad is not None else "bearing_deg: -",
      f"accuracy_m: {self.location_stdev:.1f}" if self.location_stdev is not None else "accuracy_m: -",
      f"gps_speed_mps: {self.gps_speed:.1f}",
      f"",
      f"# OSM fetch",
      f"last_fetch_loc: {self.last_fetch_location}" if self.last_fetch_location is not None else "last_fetch_loc: -",
      f"ways_fetched: {len(self.way_collection.way_relations)}" if self.way_collection is not None else "ways_fetched: 0",
      f"",
      f"# updated: {time.strftime('%H:%M:%S')} (1Hz cycle)",
    ]
    try:
      with open("/tmp/mapd_kommu_status.txt", "w") as f:
        f.write("\n".join(lines) + "\n")
    except Exception:
      pass  # never let status-file writing crash the control loop

  def publish_to_params(self):
    """Compute the corner speed and write it (plus validity) to Params."""
    budget = _read_budget(self.params)
    v_corner = _compute_corner_speed(self.route, budget, self.gps_speed)

    now = time.monotonic()
    if v_corner is not None and self.route is not None and self.route.located:
      # vCruiseMapCorner is declared FLOAT: params' strict cast table requires a native float
      # (PYTHON_2_CPP has (float, FLOAT) but NOT (str, FLOAT)); a formatted str raises TypeError.
      self.params.put_nonblocking("vCruiseMapCorner", float(v_corner))
      self.params.put_bool_nonblocking("MapCornerValid", True)
      self._last_valid_t = now
    else:
      # Hold the last valid value briefly (smooths 1 Hz GPS gaps), then invalidate.
      if now - self._last_valid_t > MAP_VALIDITY_TIMEOUT_S:
        self.params.put_bool_nonblocking("MapCornerValid", False)

    # Human-readable status file for SSH debugging. No params involved — just `cat` it.
    self._write_status_file(now, v_corner, budget)


def mapd_thread(sm=None, pm=None):
  config_realtime_process(2, 5)
  mapd = MapDKommu()
  rk = Ratekeeper(1.0, print_delay_threshold=None)  # 1 Hz — matches gpsLocation rate

  # KA2 produces `gpsLocation` (qcomgpsd). NOT gpsLocationExternal.
  # We subscribe to selfdriveState to detect engage/disengage (clears the route on disengage).
  if sm is None:
    import cereal.messaging as messaging
    sm = messaging.SubMaster(['gpsLocation', 'selfdriveState'])

  while True:
    sm.update()
    mapd.update_state(sm)
    mapd.update_gps(sm)
    mapd.updated_osm_data()
    mapd.update_route()
    mapd.publish_to_params()
    rk.keep_time()


def main(sm=None, pm=None):
  mapd_thread(sm, pm)
