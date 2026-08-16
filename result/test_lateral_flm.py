#!/usr/bin/env python3
"""
Tests for result/lateral_flm.py.

Two layers:
1. Synthetic unit tests — feed constructed samples, assert the classifier fires
   exactly where it should and stays silent where it shouldn't.
2. Real-data cross-validation (skipped if no rlog available) — verify the
   derived quantities against independent signals in the log:
   - desired_la (from controlsState.desiredCurvature) vs pidState.steeringAngleDesiredDeg
   - actual_la (camera odometry yaw) vs the steering-angle-based kinematic estimate

Run: .venv/bin/python result/test_lateral_flm.py [path-to-rlog]
"""
import math
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lateral_flm import (  # noqa: E402
  Sample, classify, direction_reversal_count, eligibility_mask, segment_samples,
)

FAILURES = []


def check(name, cond, detail=""):
  status = "PASS" if cond else "FAIL"
  print(f"  [{status}] {name}" + (f" ({detail})" if detail else ""))
  if not cond:
    FAILURES.append(name)


def make_samples(n=600, dt=0.01, v=20.0, desired_fn=lambda t: 0.0, actual_fn=lambda t, d: 0.0,
                 steering_pressed_fn=lambda t: False, seg=0, output_fn=lambda t: 0.0,
                 angle_fn=lambda t: 0.0):
  samples = []
  for k in range(n):
    t = k * dt
    d = desired_fn(t)
    jerk = 0.0
    if k > 2:
      jerk = (desired_fn(t) - desired_fn(t - 3 * dt)) / (3 * dt)
    samples.append(Sample(
      t=t, v_ego=v, desired_la=d, actual_la=actual_fn(t, d), desired_jerk=jerk,
      steering_angle_deg=angle_fn(t), angle_desired_deg=math.nan, angle_error_deg=math.nan,
      output=output_fn(t), p_term=0.0, i_term=0.0, f_term=0.0, saturated=False,
      lat_active=True, steering_pressed=steering_pressed_fn(t), segment=seg,
    ))
  return samples


def test_reversals():
  print("direction_reversal_count:")
  check("counts sign flips", direction_reversal_count(np.array([0, 1, 2, 1, 0, -1, 0, 1]), 0.1) == 2)
  check("ignores jitter below min_step",
        direction_reversal_count(np.array([0, 0.001, -0.001, 0.001, 0.0]), 0.1) == 0)
  check("monotonic has none", direction_reversal_count(np.linspace(0, 5, 50), 0.01) == 0)


def test_silence_on_perfect_tracking():
  print("classification: perfect tracking stays silent")
  # steady left curve, actual == desired, calm jerk
  s = make_samples(desired_fn=lambda t: -0.6 if 2 < t < 8 else 0.0,
                   actual_fn=lambda t, d: d if 2 < t < 8 else 0.0)
  res = classify(s)
  evts = res.get("events", {})
  check("no understeer", "understeer" not in evts, str({k: v["total"] for k, v in evts.items()}))
  check("no oversteer", "oversteer" not in evts)
  check("no chatter", "center_chatter" not in evts)


def test_understeer_detected():
  print("classification: understeer in steady curve")
  # steady curve, actual lags desired by 0.35 m/s^2 during the curve
  s = make_samples(desired_fn=lambda t: 0.6 if 2 < t < 8 else 0.0,
                   actual_fn=lambda t, d: (d - 0.35) if 2 < t < 8 else 0.0)
  res = classify(s)
  evts = res.get("events", {})
  check("understeer fired", "understeer" in evts and evts["understeer"]["total"] >= 1,
        str({k: v["total"] for k, v in evts.items()}))
  check("no oversteer", "oversteer" not in evts)


def test_late_turn_in():
  print("classification: late turn-in")
  # entry phase: desired ramping up with matching-sign jerk, actual stays near 0
  def ramp(t):
    if t < 2 or t > 8:
      return 0.0
    return 0.05 + 0.35 * (t - 2)  # rising desired, jerk ~0.35*20=... positive*positive
  s = make_samples(desired_fn=ramp, actual_fn=lambda t, d: max(d - 0.3, 0.0))
  res = classify(s)
  evts = res.get("events", {})
  check("late_turn_in fired", "late_turn_in" in evts and evts["late_turn_in"]["total"] >= 1,
        str({k: v["total"] for k, v in evts.items()}))


def test_center_chatter():
  print("classification: center chatter on a straight")
  # straight road (desired calm), steering + output oscillating at ~5 Hz so the
  # 0.4 s analysis window sees >= 3 reversals
  osc = lambda t: 0.8 * math.sin(2 * math.pi * 5.0 * t)
  s = make_samples(v=20.0, angle_fn=osc, output_fn=lambda t: 0.06 * math.sin(2 * math.pi * 5.0 * t + 0.7),
                   actual_fn=lambda t, d: 0.08 * math.sin(2 * math.pi * 5.0 * t + 0.4))
  res = classify(s)
  evts = res.get("events", {})
  check("center_chatter fired", "center_chatter" in evts and evts["center_chatter"]["total"] >= 1,
        str({k: v["total"] for k, v in evts.items()}))


def test_eligibility():
  print("eligibility mask:")
  s = make_samples()
  s[300].steering_pressed = True
  elig = eligibility_mask(s)
  check("segment boundaries forced ineligible", not elig[0] and not elig[-1])
  check("override sample ineligible", not elig[300])
  check("post-override buffer", not elig[300 + 60] and elig[300 + 150], "1s post / ok later")
  # inactive lateral excluded
  s2 = make_samples()
  for x in s2:
    x.lat_active = False
  check("lat_active=False excludes all", not any(eligibility_mask(s2)))
  # low speed excluded
  s3 = make_samples()
  for x in s3:
    x.v_ego = 1.0
  check("v_ego<=2 excluded", not any(eligibility_mask(s3)))


def test_real_data(rlog):
  print(f"real-data cross-validation: {os.path.basename(rlog)}")
  samples, car_info = segment_samples(rlog, 0)
  check("samples extracted", len(samples) > 1000, f"{len(samples)}")

  # pidState.active gating actually bites (some samples inactive). A fully-engaged
  # highway segment has zero, so fall back to a known mixed city segment.
  n_inactive = sum(1 for s in samples if not s.lat_active)
  if n_inactive == 0:
    city = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "drives", "Archive", "2026-08-10--00-30-51--2.rlog.zst")
    if os.path.exists(city):
      ss2, _ = segment_samples(city, 2)
      n_inactive = sum(1 for s in ss2 if not s.lat_active)
      n_total = len(ss2)
    else:
      n_total = len(samples)
  else:
    n_total = len(samples)
  check("lat_active gating observed", 0 < n_inactive < n_total, f"{n_inactive}/{n_total} inactive")

  # desired_la vs desired steering angle: angle ~ curv * ratio * wheelbase (rad->deg).
  # NOTE: sign conventions differ between curvature and steering angle in this stack,
  # so we assert |corr| — the relation must be strongly linear either way.
  sr, wb = car_info["steerRatio"], car_info["wheelbase"]
  pairs = [(s.desired_la / max(s.v_ego ** 2, 1e-3), s.angle_desired_deg)
           for s in samples if math.isfinite(s.angle_desired_deg) and s.lat_active and s.v_ego > 5]
  check("desired-angle pairs available", len(pairs) > 500, f"{len(pairs)}")
  curvs = np.array([p[0] for p in pairs])
  angs = np.array([p[1] for p in pairs])
  if len(pairs) > 100:
    pred = np.degrees(curvs * sr * wb)
    m = np.abs(angs) > 1.0  # only where angle is meaningful
    if m.sum() > 100:
      corr = abs(float(np.corrcoef(pred[m], angs[m])[0, 1]))
      check("desired_la ~ desired steering angle", corr > 0.9, f"|corr|={corr:.3f}")
    else:
      print("  [SKIP] drive mostly straight, not enough meaningful angle")

  # actual_la (camera yaw) vs kinematic estimate from steering angle; |corr| again
  pairs2 = [(s.actual_la,
             math.radians(s.steering_angle_deg) / (sr * wb) * s.v_ego ** 2)
            for s in samples if math.isfinite(s.actual_la) and s.lat_active and s.v_ego > 5]
  a = np.array([p[0] for p in pairs2])
  b = np.array([p[1] for p in pairs2])
  corr2 = abs(float(np.corrcoef(a, b)[0, 1]))
  check("actual_la ~ angle-based estimate", corr2 > 0.5, f"|corr|={corr2:.3f} n={len(a)}")

  # classify end-to-end and ensure JSON-serializable basics
  res = classify(samples)
  check("classify produced events dict", isinstance(res.get("events", {}), dict))


def main():
  test_reversals()
  test_silence_on_perfect_tracking()
  test_understeer_detected()
  test_late_turn_in()
  test_center_chatter()
  test_eligibility()

  rlog = sys.argv[1] if len(sys.argv) > 1 else None
  if rlog is None:
    cand = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "drives", "2026-08-09--04-01-58--17.rlog.zst")
    if os.path.exists(cand):
      rlog = cand
  if rlog and os.path.exists(rlog):
    test_real_data(rlog)
  else:
    print("real-data tests: SKIP (no rlog given/found)")

  print()
  if FAILURES:
    print(f"FAILED ({len(FAILURES)}): {FAILURES}")
    sys.exit(1)
  print("ALL TESTS PASSED")


if __name__ == "__main__":
  main()
