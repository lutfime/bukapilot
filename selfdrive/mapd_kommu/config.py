# Map query config (ported from KisaPilot/sunnypilot mapd, adapted for KA2 + Kommu)

QUERY_RADIUS = 3000  # mts. Radius to use on OSM data queries.
MIN_DISTANCE_FOR_NEW_QUERY = 1000  # mts. Minimum distance to query area edge before issuing a new query.
FULL_STOP_MAX_SPEED = 1.39  # m/s Max speed for considering car is stopped.
LOOK_AHEAD_HORIZON_TIME = 15.  # s. Time horizon for look ahead of turn speed sections.
LANE_WIDTH = 3.7  # Lane width estimate. Used for detecting departures from way.

# ---- Map corner slowdown (Kommu) ----
# Default lateral acceleration budget (m/s^2) used to compute v_corner = sqrt(budget / kappa).
# Overridable at runtime via the MapCornerBudget param (tunable from the iOS app).
DEFAULT_LAT_ACCEL_BUDGET = 2.5

# Below this corner speed (m/s) the map value is considered invalid and the planner ignores it.
# Prevents bad OSM geometry from forcing near-stops.
MIN_V_CORNER = 3.0  # ~10.8 km/h

# How long (seconds) without a valid GPS fix / located route before we mark the map value invalid.
MAP_VALIDITY_TIMEOUT_S = 10.0
