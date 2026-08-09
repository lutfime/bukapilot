#!/usr/bin/env python3
"""
test_map_corner.py — offline validation of the map-aware corner slowdown pipeline.

Feeds a lat/lon/bearing and runs the OSM fetch + route matching + curvature math
end-to-end, printing the advisory corner speed (v_corner) the planner would see.
No car required. Use this to sanity-check the mapd_kommu math before driving.

USAGE (from repo root, in the openpilot venv):
    PYTHONPATH=. /usr/local/venv/bin/python3 tools-local/test_map_corner.py \\
        --lat 3.1390 --lon 101.6869 --bearing 90

    # Or feed a sequence of points (CSV: lat,lon,bearing) to simulate a drive:
    PYTHONPATH=. /usr/local/venv/bin/python3 tools-local/test_map_corner.py \\
        --csv my_route.csv

    --budget 2.5   lateral accel budget (m/s^2) to test different aggressiveness
    --radius 3000  OSM query radius (m)

REQUIREMENTS:
    - `overpy` pip package (for the Overpass client). Install: pip install overpy
    - Internet (hits the public Overpass API).
    - Run from the repo root so `openpilot.selfdrive.mapd_kommu` imports resolve.

This is OBSERVATION ONLY — it never touches the car or writes Params.
"""
import argparse
import csv
import math
import sys

import numpy as np


def query_and_compute(lat, lon, bearing_deg, budget, radius, verbose=False):
  """Run a single OSM query + route match + corner-speed computation. Returns (v_corner, sections)."""
  from openpilot.selfdrive.mapd_kommu.lib.osm import OSM
  from openpilot.selfdrive.mapd_kommu.lib.WayCollection import WayCollection

  osm = OSM()
  if verbose:
    print(f"  Querying OSM at ({lat:.5f}, {lon:.5f}) r={radius}m ...")
  ways = osm.fetch_road_ways_around_location(lat, lon, radius)
  if verbose:
    print(f"  Got {len(ways)} ways")

  if len(ways) == 0:
    return None, []

  location_rad = np.radians(np.array([lat, lon], dtype=float))
  bearing_rad = math.radians(bearing_deg)
  stdev = 5.0  # assume decent GPS for testing

  wc = WayCollection(ways, location_rad)
  route = wc.get_route(location_rad, bearing_rad, stdev)

  if route is None or not route.located:
    if verbose:
      print(f"  Route NOT located (no matching way for bearing {bearing_deg}°)")
    return None, []

  if verbose:
    print(f"  Route located: {route}")
    rn = route.current_road_name
    if rn:
      print(f"  Road: {rn}")

  # Scale from NodesData's hardcoded _MAX_LAT_ACC=2.3 to the test budget.
  scale = math.sqrt(budget / 2.3)
  sections = route.curvature_speed_limits_ahead
  current = route.current_curvature_speed_limit_section

  candidates = []
  if current is not None:
    candidates.append(current.value * scale)
  for s in sections:
    candidates.append(s.value * scale)

  ahead = route.next_curvature_speed_limit_sections(300.0)
  for s in ahead:
    candidates.append(s.value * scale)

  if len(candidates) == 0:
    if verbose:
      print("  No curvature sections ahead (road is straight)")
    return None, []

  v_corner = min(candidates)
  return v_corner, sections


def main():
  ap = argparse.ArgumentParser(description="Offline map corner speed validator")
  ap.add_argument("--lat", type=float, help="Latitude (degrees)")
  ap.add_argument("--lon", type=float, help="Longitude (degrees)")
  ap.add_argument("--bearing", type=float, default=0.0, help="Travel bearing (degrees from north)")
  ap.add_argument("--csv", help="CSV file: lat,lon,bearing per line (simulate a drive)")
  ap.add_argument("--budget", type=float, default=2.5, help="Lateral accel budget m/s^2 (default 2.5)")
  ap.add_argument("--radius", type=int, default=3000, help="OSM query radius m (default 3000)")
  ap.add_argument("-v", "--verbose", action="store_true")
  args = ap.parse_args()

  if args.csv:
    with open(args.csv) as f:
      points = [(float(r[0]), float(r[1]), float(r[2])) for r in csv.reader(f) if len(r) >= 3]
  elif args.lat is not None and args.lon is not None:
    points = [(args.lat, args.lon, args.bearing)]
  else:
    ap.error("provide --lat/--lon/--bearing, or --csv")
    return

  print(f"map corner speed validator — budget={args.budget} m/s², radius={args.radius}m")
  print(f"{'lat':>10} {'lon':>11} {'bearing':>8}  {'v_corner (m/s)':>14} {'v_corner (km/h)':>16}  status")
  print("-" * 80)

  for lat, lon, bearing in points:
    try:
      v, sections = query_and_compute(lat, lon, bearing, args.budget, args.radius, args.verbose)
      if v is None:
        print(f"{lat:10.5f} {lon:11.5f} {bearing:8.1f}  {'--':>14} {'--':>16}  no corner / not located")
      else:
        print(f"{lat:10.5f} {lon:11.5f} {bearing:8.1f}  {v:14.2f} {v*3.6:16.1f}  OK ({len(sections)} sections)")
    except Exception as e:
      print(f"{lat:10.5f} {lon:11.5f} {bearing:8.1f}  {'ERR':>14} {'ERR':>16}  {e}")


if __name__ == "__main__":
  sys.exit(main() or 0)
