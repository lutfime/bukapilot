#!/usr/bin/env python3
"""
CurveSpeedController — ported from FrogPilot (MIT, FrogAi/FrogsGoMoo).

Uses the model's predicted road curvature (modelV2.orientationRate.z / velocity.x)
to compute a corner speed: v = sqrt(budget / curvature). Clamps v_cruise down to
this target so the MPC plans a smooth decel. Slowdown-only by min() construction.

4 profiles (MapCornerProfile param):
  GENTLE (0):   fixed 1.5 m/s² budget — cautious.
  STANDARD (1): fixed 2.0 m/s² budget — default.
  SPORT (2):    self-tuning — grows the budget toward the steering saturation limit.
  AUTO (3):     learns your personal comfort lateral-accel from how you drive corners.

No map / OSM / GPS / internet needed — runs purely from the driving model output.
"""
import json

import numpy as np

from openpilot.common.params import Params
from openpilot.common.realtime import DT_MDL
from openpilot.selfdrive.controls.lib.drive_helpers import MAX_LATERAL_ACCEL_NO_ROLL


# ---- Constants (copied from FrogPilot) ----
CRUISING_SPEED = 5.0          # m/s — don't activate below this (~18 km/h)
MINIMUM_PLANNED_SPEED = 2.0   # m/s — model points slower than this are stop-approach, not curves
MINIMUM_LATERAL_ACCELERATION = 1.3  # m/s² — below this, not "in a curve"
DEFAULT_LATERAL_ACCELERATION = 2.0  # m/s² — Standard profile budget
GENTLE_LATERAL_ACCELERATION = 1.5   # m/s² — Gentle profile budget

# Smoothing (copied from FrogPilot update_target)
TARGET_LEAD_TIME = 2.0        # s — reach target speed this far before the curve
TARGET_RISE_RATE = 1.2        # m/s per cycle — how fast target rises back up after a curve
TARGET_TRACKING_MARGIN = 1.0  # m/s — hysteresis so target doesn't hunt

# Curve detection hysteresis (ported from FrogPilot) — prevents hunt on gentle curves
CURVE_DETECTION_ENTER = 1.0   # m/s² — must exceed this to activate CSC
CURVE_DETECTION_EXIT = 0.7    # m/s² — must drop below this to deactivate

# Driver-learning calibration ("Auto" profile, copied from FrogPilot)
CALIBRATION_PROGRESS_THRESHOLD = int(10 / DT_MDL)
CALIBRATION_TARGET = CALIBRATION_PROGRESS_THRESHOLD * 50
PERCENTILE = 90
ROUNDING_PRECISION = 3

# Learned maximum lateral acceleration ("Sport" profile, copied from FrogPilot)
MAX_ANGLE_GROWTH_RATE = 0.02
MAX_BACKOFF_RATE = 0.1
MAX_BACKOFF_SAMPLES = round(1 / DT_MDL)
MAX_GROWTH_RATE = 0.08
MAX_TORQUE_HEADROOM = 0.9
MIN_LIMIT_FACTOR = 0.5

# Profiles
GENTLE = 0
STANDARD = 1
SPORT = 2
AUTO = 3


def calculate_road_curvature(modelData, v_ego, lateral_budget):
  """
  Credit: FrogPilot / Pfeiferj. Computes the road curvature ahead from the model's
  predicted yaw rate + velocity trajectory, and finds the point requiring the most
  deceleration. Returns (curvature_at_worst_point, time_to_that_point, peak_curvature).
  """
  velocity = np.array(modelData.velocity.x)
  yaw_rate = np.array(modelData.orientationRate.z)
  time_to = np.array(modelData.orientationRate.t)
  # Guard: empty or degraded model output → no curvature, safe default.
  if velocity.size == 0 or yaw_rate.size == 0 or time_to.size == 0:
    return 0.0, 0.0, 0.0
  if not (velocity.size == yaw_rate.size == time_to.size):
    return 0.0, 0.0, 0.0
  curvature = yaw_rate / np.maximum(velocity, 1)
  moving_curvature = np.where(velocity >= MINIMUM_PLANNED_SPEED, np.abs(curvature), 0)

  time_to_point = np.maximum(time_to, 1)
  required_decelerations = (v_ego - np.sqrt(lateral_budget / np.maximum(moving_curvature, 1e-6))) / time_to_point

  if lateral_budget > 0 and np.any(required_decelerations > 0):
    index = np.argmax(required_decelerations)
  else:
    index = np.argmax(moving_curvature)

  return float(curvature[index]), float(time_to_point[index]), float(np.max(moving_curvature))


class CurveSpeedController:
  def __init__(self, CP):
    self.CP = CP
    self.params = Params()

    self.enable_training = False
    self.target_set = False
    self.saturation_backoff_samples = 0
    self.training_timer = 0

    self.budget = DEFAULT_LATERAL_ACCELERATION
    self.lateral_acceleration = DEFAULT_LATERAL_ACCELERATION       # LIVE latacc (per-cycle)
    self.learned_lateral_acceleration = DEFAULT_LATERAL_ACCELERATION  # LEARNED (Auto profile budget)
    # Sport profile learned limit — persisted across restarts (FrogPilot uses MaxLateralAcceleration).
    try:
      raw_ml = self.params.get("MapCornerMaxLimit")
      self.max_limit = float(raw_ml) if raw_ml else 0.0
    except (TypeError, ValueError):
      self.max_limit = 0.0

    # learned data (Auto profile)
    self.curvature_data = self._load_curvature_data()
    self._update_lateral_acceleration()

    # per-cycle state (set in update())
    self.road_curvature = 0.0
    self.time_to_curve = 0.0
    self.road_curvature_peak = 0.0
    self.road_curvature_detected = False
    self.driving_in_curve = False
    self.tracking_lead = False
    self.target = 0.0

  # ---- Params helpers ----

  def _read_profile(self):
    """Read the CSC profile (0=Gentle, 1=Standard, 2=Sport, 3=Auto). Default Standard."""
    raw = self.params.get("MapCornerProfile")
    try:
      return int(raw) if raw is not None else STANDARD
    except (TypeError, ValueError):
      return STANDARD

  def _load_curvature_data(self):
    """Load learned curvature→latacc map from Params (Auto profile)."""
    raw = self.params.get("CurvatureData")
    if raw is None or raw == "":
      return {}
    try:
      return json.loads(raw) if isinstance(raw, str) else raw
    except Exception:
      return {}

  # ---- Learning (Auto profile) — ported from FrogPilot log_data / update_lateral_acceleration ----

  def log_data(self, long_control_active, v_ego, sm):
    """Observe how the driver takes the current corner and accumulate the calibration map."""
    self.enable_training = v_ego > CRUISING_SPEED
    self.enable_training &= not self.tracking_lead
    self.enable_training &= not long_control_active

    if self.enable_training:
      self.training_timer += DT_MDL
      if self.training_timer >= 10.0 and self.driving_in_curve and not (sm["carState"].leftBlinker or sm["carState"].rightBlinker):
        road_curvature = abs(round(sm["controlsState"].curvature, ROUNDING_PRECISION))
        key = str(road_curvature)
        if key in self.curvature_data:
          data = self.curvature_data[key]
          average = data["average"]
          count = data["count"]
          # abs() is critical: curvature is signed, so lateral_acceleration is signed.
          # Without abs(), left-hand curves (negative) would corrupt the running average.
          latacc = abs(self.lateral_acceleration)
          self.curvature_data[key] = {
            "average": ((average * count) + latacc) / (count + 1),
            "count": min(count + 1, CALIBRATION_PROGRESS_THRESHOLD),
          }
        else:
          self.curvature_data[key] = {"average": abs(self.lateral_acceleration), "count": 1}
      else:
        self.enable_training = False
    elif self.training_timer >= 10.0:
      collected = sum(data["count"] for data in self.curvature_data.values())
      progress = float(min(collected / CALIBRATION_TARGET, 1.0) * 100)
      self.params.put_nonblocking("CalibrationProgress", progress)
      self.params.put_nonblocking("CurvatureData", self.curvature_data)
      # Persist the Sport max_limit so it survives restarts.
      self.params.put_nonblocking("MapCornerMaxLimit", self.max_limit)
      self._update_lateral_acceleration()
      self.training_timer = 0
    else:
      self.training_timer = 0

  def _update_lateral_acceleration(self):
    """Set self.learned_lateral_acceleration from the learned data (90th percentile), or default."""
    if self.curvature_data:
      all_samples = [data["average"] for data in self.curvature_data.values()]
      all_counts = [data["count"] for data in self.curvature_data.values()]
      self.learned_lateral_acceleration = float(np.percentile(np.repeat(all_samples, all_counts), PERCENTILE))
    else:
      self.learned_lateral_acceleration = DEFAULT_LATERAL_ACCELERATION

  # ---- Sport self-tuning (update_max_limit) — ported from FrogPilot ----

  def update_max_limit(self, v_ego, sm):
    """Sport profile: grow the budget toward the steering saturation limit; back off on saturation."""
    lcs = sm["controlsState"].lateralControlState
    which = lcs.which()
    if which == "torqueState":
      cs = lcs.torqueState
      growth_rate = MAX_GROWTH_RATE * max(0, (MAX_TORQUE_HEADROOM - abs(cs.output)) / MAX_TORQUE_HEADROOM)
    elif which == "pidState":
      cs = lcs.pidState
      growth_rate = MAX_GROWTH_RATE * max(0, (MAX_TORQUE_HEADROOM - abs(cs.output)) / MAX_TORQUE_HEADROOM)
    elif which == "angleState":
      cs = lcs.angleState
      growth_rate = MAX_ANGLE_GROWTH_RATE
    else:
      self.saturation_backoff_samples = 0
      return

    max_lat = self.CP.maxLateralAccel if self.CP.maxLateralAccel > 0 else DEFAULT_LATERAL_ACCELERATION
    if self.max_limit <= 0:
      self.max_limit = max_lat * MAX_TORQUE_HEADROOM

    if cs.active and not sm["carState"].steeringPressed and v_ego > CRUISING_SPEED and abs(self.lateral_acceleration) >= MINIMUM_LATERAL_ACCELERATION:
      if cs.saturated:
        if self.saturation_backoff_samples < MAX_BACKOFF_SAMPLES:
          self.max_limit *= 1 - MAX_BACKOFF_RATE * DT_MDL
          self.saturation_backoff_samples += 1
      elif abs(self.lateral_acceleration) >= self.max_limit * MAX_TORQUE_HEADROOM and growth_rate > 0:
        self.saturation_backoff_samples = 0
        self.max_limit *= 1 + growth_rate * DT_MDL
      else:
        self.saturation_backoff_samples = 0
    else:
      self.saturation_backoff_samples = 0
    self.max_limit = float(np.clip(self.max_limit, max_lat * MIN_LIMIT_FACTOR, MAX_LATERAL_ACCEL_NO_ROLL))

  # ---- Budget selection (update_budget) — adapted from FrogPilot ----

  def update_budget(self):
    """Pick the lateral-accel budget based on the active profile."""
    profile = self._read_profile()
    lat = self.learned_lateral_acceleration  # AUTO profile: learned comfort value

    if profile == GENTLE:
      lat = GENTLE_LATERAL_ACCELERATION
    elif profile == STANDARD:
      lat = DEFAULT_LATERAL_ACCELERATION
    elif profile == SPORT and self.max_limit > 0:
      lat = self.max_limit

    if self.max_limit > 0:
      lat = min(lat, self.max_limit)
    self.budget = max(lat, 0)

  # ---- Target smoothing (update_target) — copied verbatim from FrogPilot ----

  def update_target(self, v_ego):
    csc_speed = max((self.budget / abs(self.road_curvature)) ** 0.5, CRUISING_SPEED)

    if not self.target_set:
      self.target_set = True
      self.target = max(v_ego, csc_speed)

    if csc_speed < self.target:
      decel_rate = max(v_ego - csc_speed, 0) / max(self.time_to_curve - TARGET_LEAD_TIME, 1)
      self.target = max(min(self.target, v_ego) - decel_rate * DT_MDL, csc_speed)
    elif v_ego <= self.target + TARGET_TRACKING_MARGIN and abs(self.lateral_acceleration) < self.budget:
      self.target = min(self.target + TARGET_RISE_RATE * DT_MDL, csc_speed)

  # ---- Main entry — called each planner cycle ----

  def update(self, sm, v_ego, v_cruise):
    """
    Compute the CSC speed target and return min(v_cruise, target).
    Slowdown-only: the returned value is never higher than v_cruise.
    """
    long_control_active = sm["carControl"].longActive

    # per-cycle state
    self.lateral_acceleration = v_ego ** 2 * sm["controlsState"].curvature
    self.road_curvature, self.time_to_curve, self.road_curvature_peak = \
      calculate_road_curvature(sm["modelV2"], v_ego, self.budget)
    self.driving_in_curve = abs(self.lateral_acceleration) >= MINIMUM_LATERAL_ACCELERATION

    # lead tracking (pause CSC learning when following a lead)
    lead = sm["radarState"].leadOne
    self.tracking_lead = lead.status and lead.dRel < 40.0

    # Hysteresis gate (ported from FrogPilot CURVE_DETECTION_ENTER/EXIT).
    # Prevents CSC from hunting in/out on gentle curves and disables it when
    # the turn signal is on (you're about to turn into a junction, not curve).
    cs = sm["carState"]
    blinkers_on = cs.leftBlinker or cs.rightBlinker
    curve_latacc = v_ego ** 2 * self.road_curvature_peak
    if not self.road_curvature_detected:
      self.road_curvature_detected = curve_latacc > CURVE_DETECTION_ENTER
    else:
      self.road_curvature_detected = curve_latacc > CURVE_DETECTION_EXIT
    self.road_curvature_detected &= v_ego > CRUISING_SPEED
    self.road_curvature_detected &= not blinkers_on

    self.update_budget()
    self.update_max_limit(v_ego, sm)

    if long_control_active and v_ego > CRUISING_SPEED and self.road_curvature_detected:
      self.update_target(v_ego)
      return min(v_cruise, self.target)
    else:
      # not active — observe for learning (Auto profile), don't clamp
      self.log_data(long_control_active, v_ego, sm)
      self.target_set = False
      self.target = v_cruise
      return v_cruise
