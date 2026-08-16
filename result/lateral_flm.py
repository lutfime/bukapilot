#!/usr/bin/env python3
"""
FLM-style lateral analyzer for bukapilot/kommu (Proton X70, PID angle lateral).

Ported from StarPilot's Firestar Lateral Method diagnosis engine (MIT):
- phase/speed-band event classification (understeer, oversteer, late/early turn-in,
  unwind too slow/fast, low-speed unwillingness, saturation, center chatter,
  curve oscillation)
- plus PID-specific summaries (p/i/f contributions, output utilization, angle error)

Standalone: needs only capnp, zstandard, numpy on the repo's cereal schemas.
No openpilot imports, no UI, agent-oriented JSON output.

Usage:
  .venv/bin/python result/lateral_flm.py result/drives/2026-08-09--04-01-58--*.rlog.zst
  .venv/bin/python result/lateral_flm.py result/drives            # all loose rlogs
"""
from __future__ import annotations

import argparse
import glob
import json
import math
import os
import re
import sys
from dataclasses import dataclass, asdict

import numpy as np
import zstandard as zstd
import zstandard
import capnp

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from cereal import log as capnp_log  # noqa: E402

# --- FLM constants (ported) ---
DRIVER_OVERRIDE_PRE_BUFFER_S = 0.35
DRIVER_OVERRIDE_POST_BUFFER_S = 1.0


def speed_band_label(v_ego: float) -> str:
  if v_ego < 6.0:
    return "low"
  if v_ego < 15.0:
    return "mid"
  if v_ego < 25.0:
    return "fast"
  return "highway"


@dataclass
class Sample:
  t: float          # seconds since segment start
  v_ego: float
  desired_la: float   # desired lateral accel m/s^2
  actual_la: float    # measured lateral accel m/s^2 (camera odometry yaw)
  desired_jerk: float
  steering_angle_deg: float
  angle_desired_deg: float
  angle_error_deg: float
  output: float       # normalized lateral output [-1, 1]
  p_term: float
  i_term: float
  f_term: float
  saturated: bool
  lat_active: bool
  steering_pressed: bool
  segment: int


def parse_rlog(path: str):
  with open(path, "rb") as f:
    try:
      dat = zstd.ZstdDecompressor().stream_reader(f).read()
    except zstandard.ZstdError:
      print(f"  WARNING: corrupt rlog skipped: {path}", file=sys.stderr)
      return
  for e in capnp_log.Event.read_multiple_bytes(dat):
    try:
      yield e
    except capnp.KjException:
      continue


def segment_samples(path: str, seg: int) -> tuple[list[Sample], dict]:
  """Extract lateral samples from one rlog segment."""
  samples: list[Sample] = []
  car_info: dict = {}
  t0 = None
  # latest-state holders
  st = {"cs": None, "co": None, "ts": None, "te": None}

  def emit(car_state):
    cs = st["cs"]
    if cs is None:
      return
    v = float(car_state.vEgo)
    des_curv = float(cs.desiredCurvature)
    desired_la = des_curv * v * v
    actual_la = math.nan
    if st["co"] is not None:
      actual_la = float(st["co"].rot[2]) * v  # yaw rate * speed
    out = p = i = f = math.nan
    sat = False
    a_des = a_err = math.nan
    lc = cs.lateralControlState
    lat_active = bool(car_state.cruiseState.enabled)
    if lc.which() == "pidState":
      ps = lc.pidState
      out, p, i, f = float(ps.output), float(ps.p), float(ps.i), float(ps.f)
      sat = bool(ps.saturated)
      a_des = float(ps.steeringAngleDesiredDeg)
      a_err = float(ps.angleError)
      lat_active = bool(ps.active)
    elif lc.which() == "torqueState":
      ts = lc.torqueState
      out = float(ts.steer)
      p = float(ts.p if hasattr(ts, "p") else math.nan)
      i = float(ts.i if hasattr(ts, "i") else math.nan)
      f = float(ts.f if hasattr(ts, "f") else math.nan)
      sat = bool(ts.saturated)
      lat_active = bool(ts.active)
    t = st["cs_t"] if st["cs_t"] is not None else 0.0
    samples.append(Sample(
      t=t, v_ego=v, desired_la=desired_la, actual_la=actual_la, desired_jerk=0.0,
      steering_angle_deg=float(car_state.steeringAngleDeg),
      angle_desired_deg=a_des, angle_error_deg=a_err, output=out,
      p_term=p, i_term=i, f_term=f, saturated=sat,
      lat_active=lat_active,
      steering_pressed=bool(car_state.steeringPressed), segment=seg,
    ))

  for e in parse_rlog(path):
    try:
      w = e.which()
    except Exception:
      continue
    if w == "carState":
      if t0 is None:
        t0 = e.logMonoTime
      st["cs_t"] = (e.logMonoTime - t0) / 1e9
      # latency-align: use latest controlsState (computed ~100Hz)
      emit(e.carState)
    elif w == "controlsState":
      st["cs"] = e.controlsState
    elif w == "cameraOdometry":
      st["co"] = e.cameraOdometry
    elif w == "carParams":
      p = e.carParams
      lt = p.lateralTuning
      car_info = {
        "carFingerprint": p.carFingerprint,
        "steerRatio": float(p.steerRatio),
        "wheelbase": float(p.wheelbase),
        "steerActuatorDelay": float(p.steerActuatorDelay),
        "lateralController": str(lt.which()),
      }
      if lt.which() == "pid":
        car_info["pid"] = {
          "kpBP": list(lt.pid.kpBP), "kpV": list(lt.pid.kpV),
          "kiBP": list(lt.pid.kiBP), "kiV": list(lt.pid.kiV),
          "kf": float(lt.pid.kf),
        }
      elif lt.which() == "torque":
        car_info["torque"] = {
          "latAccelFactor": float(lt.torque.latAccelFactor),
          "friction": float(lt.torque.friction),
        }

  # desired jerk = d(desired_la)/dt, lightly smoothed
  if len(samples) > 10:
    t_arr = np.array([s.t for s in samples])
    d_arr = np.array([s.desired_la for s in samples])
    jerk = np.gradient(np.convolve(d_arr, np.ones(5) / 5.0, mode="same"), t_arr)
    for s, j in zip(samples, jerk, strict=True):
      s.desired_jerk = float(j)
  return samples, car_info


def eligibility_mask(samples: list[Sample]) -> list[bool]:
  eligible = [bool(s.lat_active) and math.isfinite(s.actual_la) and s.v_ego > 2.0 and not s.steering_pressed
              for s in samples]
  # driver-override pre/post buffers, per FLM
  group_start = 0
  while group_start < len(samples):
    group_end = group_start + 1
    while group_end < len(samples) and samples[group_end].segment == samples[group_start].segment:
      group_end += 1
    last_override = -math.inf
    for idx in range(group_start, group_end):
      if samples[idx].steering_pressed:
        last_override = samples[idx].t
      if (samples[idx].t - last_override) <= DRIVER_OVERRIDE_POST_BUFFER_S:
        eligible[idx] = False
    next_override = math.inf
    for idx in range(group_end - 1, group_start - 1, -1):
      if samples[idx].steering_pressed:
        next_override = samples[idx].t
      if (next_override - samples[idx].t) <= DRIVER_OVERRIDE_PRE_BUFFER_S:
        eligible[idx] = False
    eligible[group_start] = False          # force boundary between segments
    eligible[group_end - 1] = False
    group_start = group_end
  return eligible


def direction_reversal_count(values: np.ndarray, min_step: float) -> int:
  changes = 0
  last = 0.0
  for dv in np.diff(values):
    s = math.copysign(1.0, dv) if abs(dv) >= min_step else 0.0
    if s == 0.0:
      continue
    if last != 0.0 and s != last:
      changes += 1
    last = s
  return changes


def group_masked_events(samples, mask, score_series, min_points=5):
  events = []
  start_idx = None
  for idx, active in enumerate(list(mask) + [False]):
    if active and start_idx is None:
      start_idx = idx
      continue
    if active:
      continue
    if start_idx is None:
      continue
    end_idx = idx - 1
    ev_samples = samples[start_idx:end_idx + 1]
    if len(ev_samples) >= min_points:
      scores = score_series[start_idx:end_idx + 1]
      peak_off = int(np.argmax(scores))
      peak = ev_samples[peak_off]
      mean_des = float(np.mean([s.desired_la for s in ev_samples]))
      direction = "left" if mean_des > 0.02 else ("right" if mean_des < -0.02 else "center")
      events.append({
        "startIdx": start_idx, "endIdx": end_idx,
        "tStart": round(ev_samples[0].t, 2),
        "segment": ev_samples[0].segment,
        "peakScore": round(float(scores[peak_off]), 3),
        "durationS": round(ev_samples[-1].t - ev_samples[0].t, 2),
        "speedBand": speed_band_label(float(np.mean([s.v_ego for s in ev_samples]))),
        "direction": direction,
        "meanSpeedKph": round(float(np.mean([s.v_ego for s in ev_samples])) * 3.6, 1),
      })
    start_idx = None
  return events


def summarize_events(bucket: str, events: list[dict]) -> dict:
  if not events:
    return {}
  by_band: dict[str, list[dict]] = {}
  for ev in events:
    by_band.setdefault(ev["speedBand"], []).append(ev)
  out = {}
  for band, evs in sorted(by_band.items()):
    scores = [e["peakScore"] for e in evs]
    out[band] = {
      "count": len(evs),
      "meanSeverity": round(float(np.mean(scores)), 3),
      "maxSeverity": round(float(np.max(scores)), 3),
      "worst": max(evs, key=lambda e: e["peakScore"]),
    }
  return {"total": len(events), "bySpeedBand": out}


def classify(samples: list[Sample]) -> dict:
  eligibility = eligibility_mask(samples)
  active = [s for s, ok in zip(samples, eligibility, strict=True) if ok]
  res = {"eligibleSampleCount": len(active)}
  if len(active) < 50:
    res["note"] = "insufficient eligible samples"
    return res

  # per-sample series over the FULL timeline (phases use desired/jerk which are
  # valid everywhere); masks gated by eligibility like FLM
  desired = np.array([s.desired_la for s in samples])
  jerk = np.array([s.desired_jerk for s in samples])
  actual = np.array([s.actual_la if math.isfinite(s.actual_la) else 0.0 for s in samples])
  over_error = np.abs(actual) - np.abs(desired)

  entry_phase = (np.abs(desired) > 0.30) & (np.abs(jerk) > 0.25) & (desired * jerk > 0.0)
  unwind_phase = (np.abs(desired) > 0.25) & (np.abs(jerk) > 0.20) & (desired * jerk < 0.0)
  steady_phase = (np.abs(desired) > 0.35) & (np.abs(jerk) < 0.18)
  elig = np.array(eligibility)
  sat_phase = elig & np.array([s.saturated for s in samples]) & (np.abs(desired) > 0.30) & \
      ((np.abs(jerk) > 0.16) | (np.abs(actual) > 0.45))

  masks = {
    "understeer": elig & steady_phase & (over_error < -0.20),
    "oversteer": elig & steady_phase & (over_error > 0.20),
    "late_turn_in": elig & entry_phase & (over_error < -0.16),
    "early_turn_in": elig & entry_phase & (over_error > 0.16),
    "unwind_too_slow": elig & unwind_phase & (over_error > 0.14),
    "unwind_too_fast": elig & unwind_phase & (over_error < -0.14),
    "low_speed_unwillingness": elig & np.array([
      s.v_ego < 6.0 and abs(s.desired_la) > 0.30 and abs(s.desired_jerk) > 0.18 and
      (abs(s.actual_la) + 0.18) < abs(s.desired_la) for s in samples]),
    "saturation_limited": sat_phase,
  }
  score_map = {
    "understeer": np.maximum(-over_error, 0.0),
    "oversteer": np.maximum(over_error, 0.0),
    "late_turn_in": np.maximum(-over_error, 0.0) + np.abs(jerk) * 0.1,
    "early_turn_in": np.maximum(over_error, 0.0) + np.abs(jerk) * 0.1,
    "unwind_too_slow": np.maximum(over_error, 0.0),
    "unwind_too_fast": np.maximum(-over_error, 0.0),
    "low_speed_unwillingness": np.array([max(abs(d) - abs(s.actual_la), 0.0) if math.isfinite(s.actual_la) else 0.0
                                         for d, s in zip(desired, samples, strict=True)]),
    "saturation_limited": np.array([1.0 if s.saturated else 0.0 for s in samples]),
  }

  events_out = {}
  for bucket, mask in masks.items():
    evs = group_masked_events(samples, mask, score_map[bucket])
    summ = summarize_events(bucket, evs)
    if summ:
      events_out[bucket] = summ

  # --- center chatter on straights (per speed band), ported from FLM ---
  angle_thresholds = {"low": 0.80, "mid": 0.55, "fast": 0.38, "highway": 0.28}
  error_thresholds = {"low": 0.16, "mid": 0.12, "fast": 0.09, "highway": 0.07}
  output_thresholds = {"low": 0.055, "mid": 0.045, "fast": 0.035, "highway": 0.025}
  chatter_events = []
  for start_idx in range(0, max(len(samples) - 20, 1), 10):
    window = samples[start_idx:start_idx + 40]
    if len(window) < 20 or not all(eligibility[start_idx:start_idx + len(window)]):
      continue
    mean_speed = float(np.mean([s.v_ego for s in window]))
    if mean_speed < 2.0:
      continue
    band = speed_band_label(mean_speed)
    des = np.array([s.desired_la for s in window])
    if float(np.mean(np.abs(des))) > (0.14 if band == "low" else 0.18):
      continue
    if float(np.ptp(des)) > 0.18 or direction_reversal_count(des, 0.008) > 3:
      continue
    ang = np.array([s.steering_angle_deg for s in window])
    centered = ang - np.linspace(ang[0], ang[-1], len(ang))
    err = np.array([(s.actual_la - s.desired_la) if math.isfinite(s.actual_la) else 0.0 for s in window])
    outp = np.array([s.output if math.isfinite(s.output) else 0.0 for s in window])
    a_p2p, e_p2p, o_p2p = float(np.ptp(centered)), float(np.ptp(err)), float(np.ptp(outp))
    a_rev = direction_reversal_count(centered, max(angle_thresholds[band] * 0.08, 0.025))
    e_rev = direction_reversal_count(err, max(error_thresholds[band] * 0.08, 0.006))
    o_rev = direction_reversal_count(outp, max(output_thresholds[band] * 0.08, 0.002))
    if (a_p2p >= angle_thresholds[band] and a_rev >= 3) and \
       ((e_p2p >= error_thresholds[band] and e_rev >= 3) or (o_p2p >= output_thresholds[band] and o_rev >= 3)):
      score = min(1.5, 0.30 * (a_p2p / angle_thresholds[band]) + 0.18 * (e_p2p / error_thresholds[band])
                  + 0.18 * (o_p2p / output_thresholds[band]) + 0.025 * min(a_rev + e_rev + o_rev, 14))
      chatter_events.append({
        "tStart": round(window[0].t, 2), "segment": window[0].segment,
        "peakScore": round(score, 3), "durationS": round(window[-1].t - window[0].t, 2),
        "speedBand": band, "direction": "center", "meanSpeedKph": round(mean_speed * 3.6, 1),
        "angleP2P": round(a_p2p, 3), "errorP2P": round(e_p2p, 3), "outputP2P": round(o_p2p, 4),
        "reversals": a_rev + e_rev + o_rev,
      })
  if chatter_events:
    events_out["center_chatter"] = summarize_events("center_chatter", chatter_events)

  # --- curve oscillation (highway curves) ---
  curve_events = []
  for start_idx in range(0, max(len(samples) - 20, 1), 8):
    window = samples[start_idx:start_idx + 36]
    if len(window) < 18 or not all(eligibility[start_idx:start_idx + len(window)]):
      continue
    if float(np.mean([s.v_ego for s in window])) < 15.0:
      continue
    sign_ref = float(np.mean([s.desired_la for s in window]))
    if abs(sign_ref) < 0.35 or any((s.desired_la * sign_ref) < 0.0 for s in window):
      continue
    err = np.array([(s.actual_la - s.desired_la) if math.isfinite(s.actual_la) else 0.0 for s in window])
    sign_changes = int(np.sum(np.sign(err[1:]) != np.sign(err[:-1])))
    amplitude = float(np.max(err) - np.min(err))
    if amplitude > 0.22 and sign_changes >= 4:
      curve_events.append({
        "tStart": round(window[0].t, 2), "segment": window[0].segment,
        "peakScore": round(amplitude + sign_changes * 0.03, 3),
        "durationS": round(window[-1].t - window[0].t, 2),
        "speedBand": speed_band_label(float(np.mean([s.v_ego for s in window]))),
        "direction": "left" if sign_ref > 0 else "right",
        "meanSpeedKph": round(float(np.mean([s.v_ego for s in window])) * 3.6, 1),
      })
  if curve_events:
    events_out["curve_oscillation"] = summarize_events("curve_oscillation", curve_events)

  res["events"] = events_out
  return res


def pid_diagnostics(samples: list[Sample]) -> dict:
  elig = eligibility_mask(samples)
  act = [s for s, ok in zip(samples, elig, strict=True) if ok and math.isfinite(s.output)]
  if len(act) < 50:
    return {}
  v = np.array([s.v_ego for s in act])
  out = np.abs(np.array([s.output for s in act]))
  p = np.array([s.p_term for s in act])
  i = np.array([s.i_term for s in act])
  f = np.array([s.f_term for s in act])
  bands = {"low": (0, 6), "mid": (6, 15), "fast": (15, 25), "highway": (25, 999)}
  out_by_band = {}
  for band, (lo, hi) in bands.items():
    m = (v >= lo) & (v < hi)
    if m.sum() < 50:
      continue
    ae = np.array([abs(s.angle_error_deg) for s, keep in zip(act, m, strict=True) if keep
                   and math.isfinite(s.angle_error_deg)])
    out_by_band[band] = {
      "samples": int(m.sum()),
      "meanUtilization": round(float(np.mean(out[m])), 3),
      "p95Utilization": round(float(np.percentile(out[m], 95)), 3),
      "satFraction": round(float(np.mean([s.saturated for s, keep in zip(act, m, strict=True) if keep])), 4),
      "angleErrRmsDeg": round(float(np.sqrt(np.mean(ae ** 2))), 3) if len(ae) else None,
      "pFrac": round(float(np.mean(np.abs(p[m])) / max(np.mean(np.abs(p[m]) + np.abs(i[m]) + np.abs(f[m])), 1e-9)), 3),
      "iFrac": round(float(np.mean(np.abs(i[m])) / max(np.mean(np.abs(p[m]) + np.abs(i[m]) + np.abs(f[m])), 1e-9)), 3),
      "fFrac": round(float(np.mean(np.abs(f[m])) / max(np.mean(np.abs(p[m]) + np.abs(i[m]) + np.abs(f[m])), 1e-9)), 3),
    }
  return {
    "outputUtilizationByBand": out_by_band,
    "saturationTotal": int(sum(1 for s in act if s.saturated)),
  }


def analyze_route(route: str, seg_files: list[tuple[int, str]]) -> dict:
  all_samples: list[Sample] = []
  car_info = {}
  t_off = 0.0
  for seg, path in sorted(seg_files):
    ss, ci = segment_samples(path, seg)
    if ci:
      car_info = ci
    seg_dur = max(ss[-1].t - ss[0].t, 0.0) if ss else 0.0
    for s in ss:
      s.t = s.t - ss[0].t + t_off
    t_off += seg_dur
    all_samples.extend(ss)
  report = {
    "route": route,
    "segments": [s for s, _ in seg_files],
    "durationMin": round(t_off / 60.0, 1),
    "sampleCount": len(all_samples),
    "car": car_info,
  }
  report.update(classify(all_samples))
  report["pidDiagnostics"] = pid_diagnostics(all_samples)
  return report


def main() -> None:
  ap = argparse.ArgumentParser(description="FLM-style lateral analyzer for kommu drives")
  ap.add_argument("paths", nargs="+", help="rlog.zst files, or a directory containing them")
  ap.add_argument("--out", default=None, help="output JSON path (default: result/flm_reports/<route>.json)")
  args = ap.parse_args()

  files: list[str] = []
  for p in args.paths:
    if os.path.isdir(p):
      files.extend(glob.glob(os.path.join(p, "*.rlog.zst")))
    else:
      files.append(p)

  routes: dict[str, list[tuple[int, str]]] = {}
  for f in files:
    base = os.path.basename(f).replace(".rlog.zst", "")
    m = re.match(r"^(.*)--(\d+)$", base)
    if m:
      routes.setdefault(m.group(1), []).append((int(m.group(2)), f))
    else:
      routes.setdefault(base, []).append((-1, f))

  out_dir = args.out or os.path.join(os.path.dirname(os.path.abspath(__file__)), "flm_reports")
  os.makedirs(out_dir, exist_ok=True)

  for route, segs in sorted(routes.items()):
    print(f"analyzing {route} ({len(segs)} segments)...", flush=True)
    report = analyze_route(route, segs)
    out_path = os.path.join(out_dir, f"{route.replace('/', '_')}.json")
    with open(out_path, "w") as fp:
      json.dump(report, fp, indent=2)
    # human summary
    print(f"  car: {report['car'].get('carFingerprint')} ({report['car'].get('lateralController')}) | "
          f"{report['durationMin']} min | {report['sampleCount']} samples | "
          f"{report.get('eligibleSampleCount', 0)} eligible")
    for ev, summ in report.get("events", {}).items():
      bands = ", ".join(f"{b}:{d['count']}(sev {d['meanSeverity']})" for b, d in summ["bySpeedBand"].items())
      print(f"  {ev}: total {summ['total']}  [{bands}]")
    pid = report.get("pidDiagnostics", {})
    for band, d in pid.get("outputUtilizationByBand", {}).items():
      print(f"  pid[{band}]: util {d['meanUtilization']} (p95 {d['p95Utilization']}) "
            f"p/i/f {d['pFrac']}/{d['iFrac']}/{d['fFrac']} angErrRms {d['angleErrRmsDeg']}deg")
    print(f"  -> {out_path}")


if __name__ == "__main__":
  main()
