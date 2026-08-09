"""On-disk cache for OSM Overpass responses.

Avoids re-downloading the same roads on every openpilot restart. Each Overpass
response (a list of way objects) is serialized to JSON keyed by a ~2 km snap grid.
The cache is capped at MAX_BYTES; LRU eviction (oldest mtime first) keeps it bounded.

This is purely an optimization: if a cell isn't cached, the caller falls through to
a live Overpass fetch. A missing/corrupt cache file is never fatal.

## Design: one file per snapped 2 km cell

The re-fetch logic in mapd.py triggers a new Overpass query every ~2 km of driving
(QUERY_RADIUS - MIN_DISTANCE_FOR_NEW_QUERY = 3000 - 1000 = 2000 m). The exact trigger
point shifts between drives (depends on speed, GPS-lock timing, start position).

To make re-driving the SAME route hit the cache reliably, we snap the query center to
a fixed 2 km grid. Any query fired within ~1 km of a previous one snaps to the same
grid point → same file → cache hit.

One file per snap point (NOT 36 copies like a naive multi-cell design), so:
  - 100 MB cap / ~500 KB per query = ~200 files = ~400 km of unique routes cached.
  - A 50 km commute uses ~25 files (~12 MB), well within budget.

Cache layout:
  CACHE_DIR/
    n3.135_e101.532.json   # snapped cell at 3.135°N, 101.532°E
    ...
  CACHE_DIR/_meta.json     # { "size_bytes": N, "version": 2 }
"""
import json
import math
import os
import time

from openpilot.common.swaglog import cloudlog

CACHE_DIR = "/data/mapd_cache"
MAX_BYTES = 100 * 1024 * 1024  # 100 MB cap
SNAP_KM = 2.0                  # snap grid size; matches the ~2 km re-fetch spacing
CACHE_VERSION = 2


def _snap(lat, lon):
  """Snap a (lat, lon) to the SNAP_KM grid. Deterministic: the same physical area
  always maps to the same snap point regardless of GPS jitter or trigger timing."""
  snap_deg_lat = SNAP_KM / 111.0
  cos_lat = max(math.cos(math.radians(lat)), 0.01)
  snap_deg_lon = SNAP_KM / (111.0 * cos_lat)
  lat_s = round(lat / snap_deg_lat) * snap_deg_lat
  lon_s = round(lon / snap_deg_lon) * snap_deg_lon
  return lat_s, lon_s


def _cell_key(lat, lon):
  """Filesystem-safe key for the snapped cell containing (lat, lon)."""
  lat_s, lon_s = _snap(lat, lon)
  def fmt(x, prefix_pos, prefix_neg):
    return f"{prefix_pos if x >= 0 else prefix_neg}{abs(x):.3f}"
  return f"{fmt(lat_s, 'n', 's')}{fmt(lon_s, 'e', 'w')}"


def _cell_path(lat, lon):
  return os.path.join(CACHE_DIR, f"{_cell_key(lat, lon)}.json")


def _meta_path():
  return os.path.join(CACHE_DIR, "_meta.json")


def _load_meta():
  try:
    with open(_meta_path()) as f:
      m = json.load(f)
      if m.get("version") != CACHE_VERSION:
        return {"size_bytes": 0, "version": CACHE_VERSION}
      return m
  except Exception:
    return {"size_bytes": 0, "version": CACHE_VERSION}


def _save_meta(m):
  try:
    with open(_meta_path(), "w") as f:
      json.dump(m, f)
  except Exception:
    pass


def _ensure_dir():
  try:
    os.makedirs(CACHE_DIR, exist_ok=True)
  except Exception as e:
    cloudlog.warning(f"osm_cache: cannot create {CACHE_DIR}: {e}")


def _evict_if_needed(target_size):
  """If cache exceeds MAX_BYTES, evict oldest cells (by mtime) until under cap."""
  try:
    meta = _load_meta()
    if meta.get("size_bytes", 0) <= MAX_BYTES:
      return
    files = []
    for name in os.listdir(CACHE_DIR):
      if not name.endswith(".json") or name.startswith("_"):
        continue
      p = os.path.join(CACHE_DIR, name)
      try:
        st = os.stat(p)
        files.append((p, st.st_mtime, st.st_size))
      except Exception:
        continue
    files.sort(key=lambda x: x[1])  # oldest mtime first
    for path, _, size in files:
      if meta.get("size_bytes", 0) <= target_size:
        break
      try:
        os.remove(path)
        meta["size_bytes"] = max(0, meta.get("size_bytes", 0) - size)
      except Exception:
        continue
    _save_meta(meta)
    cloudlog.info(f"osm_cache: evicted down to {meta.get('size_bytes', 0)} bytes")
  except Exception as e:
    cloudlog.warning(f"osm_cache: eviction error: {e}")


def _ways_to_json(ways):
  """Serialize a list of way objects to a JSON-able structure."""
  out = []
  for w in ways:
    nodes = [{"id": n.id, "lat": float(n.lat), "lon": float(n.lon)} for n in w.nodes]
    out.append({"id": w.id, "tags": dict(w.tags), "nodes": nodes})
  return out


def _json_to_ways(data):
  """Reconstruct lightweight way objects from JSON. Duck-typed to be compatible with
  WayCollection/WayRelation: .id, .tags, .nodes[].id/.lat/.lon."""
  class _N:
    __slots__ = ("id", "lat", "lon")
    def __init__(self, d):
      self.id = d["id"]; self.lat = d["lat"]; self.lon = d["lon"]
  class _W:
    __slots__ = ("id", "tags", "nodes")
    def __init__(self, d):
      self.id = d["id"]; self.tags = d["tags"]; self.nodes = [_N(n) for n in d["nodes"]]
  return [_W(d) for d in data]


def get_cached(lat, lon, max_age_hours=24 * 7):
  """Return cached ways for the snapped cell containing (lat, lon), or None if not
  cached / stale / corrupt.

  Falls back to the 8 neighboring snap cells if the home cell misses — this handles
  the case where the query center is near a grid boundary and the data we need was
  written to an adjacent cell on a previous drive. max_age_hours: refresh cells older
  than this (default 7 days). Returns None if no usable cell is found so the caller
  re-fetches; the fresh result is then written back via put()."""
  # Try the home cell first, then neighbors.
  home_lat, home_lon = _snap(lat, lon)
  snap_deg_lat = SNAP_KM / 111.0
  cos_lat = max(math.cos(math.radians(lat)), 0.01)
  snap_deg_lon = SNAP_KM / (111.0 * cos_lat)
  candidates = [(home_lat, home_lon)]
  for dlat in (-snap_deg_lat, 0, snap_deg_lat):
    for dlon in (-snap_deg_lon, 0, snap_deg_lon):
      if dlat == 0 and dlon == 0:
        continue
      candidates.append((home_lat + dlat, home_lon + dlon))

  for (c_lat, c_lon) in candidates:
    path = os.path.join(CACHE_DIR, f"{_cell_key(c_lat, c_lon)}.json")
    try:
      st = os.stat(path)
      age_h = (time.time() - st.st_mtime) / 3600.0
      if age_h > max_age_hours:
        continue  # stale → skip, try next neighbor
      with open(path) as f:
        data = json.load(f)
      if isinstance(data, list) and len(data) > 0:
        return _json_to_ways(data)
    except Exception:
      continue
  return None


def put(lat, lon, ways):
  """Cache an Overpass response under the snapped cell for (lat, lon).
  One file per snap point. Best-effort: never raises."""
  if not ways:
    return
  _ensure_dir()
  path = _cell_path(lat, lon)
  try:
    blob = json.dumps(_ways_to_json(ways))
    prev_size = 0
    try:
      prev_size = os.path.getsize(path)
    except Exception:
      pass
    with open(path, "w") as f:
      f.write(blob)
    os.utime(path, None)  # touch mtime for LRU
    meta = _load_meta()
    meta["size_bytes"] = max(0, meta.get("size_bytes", 0) - prev_size) + len(blob)
    meta["version"] = CACHE_VERSION
    _save_meta(meta)
    _evict_if_needed(MAX_BYTES)
  except Exception as e:
    cloudlog.warning(f"osm_cache: write failed for {path}: {e}")


def cache_stats():
  """Return (cell_count, size_bytes) for status display. Best-effort."""
  try:
    count = 0
    size = 0
    for name in os.listdir(CACHE_DIR):
      if name.endswith(".json") and not name.startswith("_"):
        count += 1
        try:
          size += os.path.getsize(os.path.join(CACHE_DIR, name))
        except Exception:
          pass
    return count, size
  except Exception:
    return 0, 0
