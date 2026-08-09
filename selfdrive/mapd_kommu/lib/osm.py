import numpy as np
from openpilot.selfdrive.mapd_kommu.lib.geo import R
from openpilot.common.swaglog import cloudlog

import overpy


# Public Overpass endpoints. We try them in order — mapd sticks with the last one that
# succeeded, so the FIRST entry is the default. Order = fastest first (measured 2026-08-09
# over the device's LTE): overpass-api.de ~1.4s, z mirror ~1.3s, kumi ~10s. kumi is kept as a
# last-resort fallback (it works, just slowly); leading with it stalled mapd ~10s per query.
# Queries are infrequent (~1 per km of driving thanks to tile caching in mapd.py), so the
# official endpoints' rate limits are rarely hit; a 429 fails over to the next entry instantly.
_OVERPASS_ENDPOINTS = [
  'https://overpass-api.de/api/interpreter',          # official main — fast, reliable
  'https://z.overpass-api.de/api/interpreter',        # official mirror — fast
  'https://overpass.kumi.systems/api/interpreter',    # kumi mirror — slow (~10s); last resort
]


def create_way(way_id, node_ids, from_way):
  """
  Creates and OSM Way with the given `way_id` and list of `node_ids`, copying attributes and tags from `from_way`
  """
  return overpy.Way(way_id, node_ids=node_ids, attributes={}, result=from_way._result,
                    tags=from_way.tags)


class OSM():
  def __init__(self):
    self._apis = [overpy.Overpass(url=url) for url in _OVERPASS_ENDPOINTS]
    self._last_good_idx = 0  # stick with the last endpoint that worked

  def fetch_road_ways_around_location(self, lat, lon, radius):
    # Calculate the bounding box coordinates for the bbox containing the circle around location.
    bbox_angle = np.degrees(radius / R)
    bbox_str = f'{str(lat - bbox_angle)},{str(lon - bbox_angle)},{str(lat + bbox_angle)},{str(lon + bbox_angle)}'
    q = """
        way(""" + bbox_str + """)
          [highway]
          [highway!~"^(footway|path|corridor|bridleway|steps|cycleway|construction|bus_guideway|escape|service|track)$"];
        (._;>;);
        out;
        """

    # Try the last-good endpoint first, then the rest.
    order = [self._last_good_idx] + [i for i in range(len(self._apis)) if i != self._last_good_idx]
    for idx in order:
      try:
        ways = self._apis[idx].query(q).ways
        self._last_good_idx = idx
        cloudlog.info(f"mapd_kommu: OSM query ok ({len(ways)} ways) via endpoint {idx}")
        return ways
      except Exception as e:
        cloudlog.warning(f"mapd_kommu: OSM endpoint {idx} failed: {e}")
        continue

    cloudlog.warning("mapd_kommu: all OSM endpoints failed")
    return []
