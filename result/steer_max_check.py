#!/usr/bin/env python3
"""Confirm STEER_MAX (torqueV[0]=500) for the Proton X70 from a saved log.

What 500 is:  CP.lateralParams.torqueV[0] -> STEER_MAX (proton/values.py:119),
              the hard clamp on the raw steering-torque command sent over CAN
              (apply_proton_steer_torque_limits, carcontroller.py). DBC STEER_CMD
              hardware max is 598, so 500 is a ~16% margin.

What we check from data:
  - carControl.actuators.torqueOutputCan : raw torque actually sent over CAN (the
                                           post-clamp value). Should be bounded by 500.
  - carControl.actuators.torque          : normalized command [0..1] (PID output / STEER_MAX).
  - controlsState pidState.output        : raw PID output (pre-normalize).
  - controlsState pidState.saturated     : is the PID railing?
  - carState.steeringTorque              : measured EPS torque (Nm, different units).
If torqueOutputCan ever reaches +/-500 during engaged cornering, STEER_MAX is the
limiting factor (raising it toward 598 would give more authority). If it stays well
below, 500 has plenty of headroom and is conservative.
"""
import sys, numpy as np
sys.path.insert(0, "/Users/WanLutfi/Documents/Xcode/Kommu Lutfi")
from cereal import log as capnp_log
EV = capnp_log.Event

STEER_MAX = 500.0  # from interface.py torqueV

def parse(path):
  with open(path, "rb") as f:
    return list(EV.read_multiple_bytes(f.read()))

def safe(o, *path, default=float("nan")):
  cur = o
  for p in path:
    try:
      cur = getattr(cur, p)
    except Exception:
      return default
  return cur

def main():
  path = sys.argv[1]
  ev = parse(path)
  cc_d = {"t": [], "torque": [], "torqueOut": [], "steerAngle": [], "latActive": [], "accel": []}
  cs_d = {"t": [], "vEgo": [], "steerTorque": [], "steerAngle": []}
  pid_d = {"t": [], "output": [], "saturated": []}
  for e in ev:
    try: w = e.which()
    except Exception: continue
    t = (e.logMonoTime or 0) / 1e9
    if w == "carControl":
      cc = e.carControl; a = cc.actuators
      cc_d["t"].append(t)
      cc_d["torque"].append(float(safe(a, "torque")))           # @2 normalized
      cc_d["torqueOut"].append(float(safe(a, "torqueOutputCan")))  # @8 raw CAN
      cc_d["steerAngle"].append(float(safe(a, "steeringAngleDeg")))  # @3
      cc_d["latActive"].append(bool(safe(cc, "latActive")))
      cc_d["accel"].append(float(safe(a, "accel")))
    elif w == "carState":
      cs = e.carState
      cs_d["t"].append(t); cs_d["vEgo"].append(float(safe(cs, "vEgo")))
      cs_d["steerTorque"].append(float(safe(cs, "steeringTorque")))
      cs_d["steerAngle"].append(float(safe(cs, "steeringAngleDeg")))
    elif w == "controlsState":
      lc = safe(e.controlsState, "lateralControlState")
      lw = safe(lc, "which"); lw = lw() if callable(lw) else lw
      pid = safe(lc, "pidState") if lw == "pidState" else None
      if pid is not None:
        pid_d["t"].append(t)
        pid_d["output"].append(float(safe(pid, "output")))
        pid_d["saturated"].append(bool(safe(pid, "saturated")))

  def arr(d, k): return np.array([x for x in d[k]])
  for name, d in (("torque", cc_d), ("torqueOut", cc_d), ("steerAngle", cc_d),
                  ("vEgo", cs_d), ("steerTorque", cs_d)):
    pass

  cc_t = np.array(cc_d["t"]); cs_t = np.array(cs_d["t"])
  vEgo = np.interp(cc_t, cs_t, cs_d["vEgo"]) if len(cs_t) else np.array([])
  lat = np.array(cc_d["latActive"], dtype=bool)
  eng = lat & (vEgo > 5.0) if len(vEgo) else np.zeros(len(cc_t), dtype=bool)

  print(f"file: {path}")
  print(f"carControl:{len(cc_t)}  carState:{len(cs_t)}  pid:{len(pid_d['t'])} events")
  print(f"engaged+vEgo>5: {eng.sum()}/{len(cc_t)} ({100*eng.sum()/max(1,len(cc_t)):.0f}%)")
  if eng.sum() < 20:
    print("not enough engaged data"); return

  def stats(label, vals, mask):
    v = np.asarray(vals)[mask]
    v = v[np.isfinite(v)]
    if len(v) == 0:
      print(f"  {label:22s}: (no data)"); return
    absv = np.abs(v)
    pct = lambda p: float(np.percentile(absv, p))
    print(f"  {label:22s}: n={len(v):5d}  min={v.min():8.1f} max={v.max():8.1f} "
          f"|p50|={pct(50):7.1f} |p95|={pct(95):7.1f} |p99|={pct(99):7.1f} |max|={absv.max():7.1f}")

  print("\n=== engaged+vEgo>5 ranges ===")
  stats("torque (norm 0..1)", cc_d["torque"], eng)
  stats("torqueOutputCan", cc_d["torqueOut"], eng)
  stats("actuators.steerAngleDeg", cc_d["steerAngle"], eng)
  stats("carState.steeringTorque(Nm)", cs_d["steerTorque"], np.interp(cs_t, cc_t, eng.astype(float)) > 0.5 if len(cc_t) else np.zeros(len(cs_t), bool))

  # saturation analysis on torqueOutputCan
  to = np.asarray(cc_d["torqueOut"], dtype=float)
  fin = np.isfinite(to) & (np.abs(to) > 0)  # ignore all-zero/empty field
  if fin.any():
    absto = np.abs(to[fin & eng if (fin & eng).any() else fin])
    print(f"\n=== STEER_MAX saturation (raw CAN torque; ceiling = {STEER_MAX:.0f}, DBC max = 598) ===")
    print(f"  max |torqueOutputCan| seen      : {absto.max():.0f}")
    for thr in (0.9, 0.95, 0.99):
      print(f"  within {int(thr*100)}% of {STEER_MAX:.0f} (>{STEER_MAX*thr:.0f}): "
            f"{100*(absto >= STEER_MAX*thr).sum()/max(1,len(absto)):.1f}% of engaged frames")
    print(f"  -> {'HITS ceiling: STEER_MAX is the limiting factor (consider 598 for more authority)'
            if absto.max() >= STEER_MAX*0.95 else 'lots of headroom: 500 is conservative, not limiting'}")
  else:
    print("\n(torqueOutputCan not present in this log schema — using normalized torque instead)")
    tq = np.asarray(cc_d["torque"], dtype=float); tq = tq[eng & np.isfinite(tq)]
    if len(tq):
      print(f"  max |normalized torque| : {np.abs(tq).max():.3f}  (1.0 = STEER_MAX={STEER_MAX:.0f})")
      print(f"  p99 |normalized torque| : {np.percentile(np.abs(tq),99):.3f}")

  if pid_d["output"]:
    po = np.array(pid_d["output"]); ps = np.array(pid_d["saturated"], dtype=bool)
    print(f"\n=== PID (lateral) ===")
    print(f"  pid.output range : {po.min():.1f} .. {po.max():.1f}  (saturated {100*ps.sum()/max(1,len(ps)):.1f}% of frames)")

if __name__ == "__main__":
  main()
