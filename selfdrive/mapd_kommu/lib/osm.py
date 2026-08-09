import numpy as np
from openpilot.selfdrive.mapd_kommu.lib.geo import R
from openpilot.common.swaglog import cloudlog

import overpy


# Public Overpass endpoints. We try them in order — public endpoints are often rate-limited
# or overloaded, so fallback matters. On the device this runs over LTE; queries are infrequent
# (~1 per km of driving thanks to tile caching in mapd.py), so rate limits are rarely hit.
_OVERPASS_ENDPOINTS = [
  'https://overpass.kumi.systems/api/interpreter',   # reliable, good global coverage
  'https://overpass-api.de/api/interpreter',          # official main
  'https://z.overpass-api.de/api/interpreter',        # official mirror
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
