#!/usr/bin/env python3
"""Measure steering actuator delay from an openpilot qlog.

Delay = how much the ACTUAL steering (carState.steeringAngleDeg) trails the COMMANDED
steer (carControl.actuators.steer), on frames where openpilot is laterally engaged and
the car is moving. Same measurement PlotJuggler's tuning.xml is used for, computed directly.

Method: on each contiguous engaged+moving run, brute-force the lag (0..0.8s) that best
aligns the commanded steer with the actual angle (max correlation). Report per-run + median.
"""
import sys, numpy as np
sys.path.insert(0, "/Users/WanLutfi/Documents/Xcode/Kommu Lutfi")
from cereal import log as capnp_log
EV = capnp_log.Event

def parse(path):
  with open(path, "rb") as f:
    return list(EV.read_multiple_bytes(f.read()))

def extract(events):
  cc_t, steer, lat = [], [], []
  cs_t, angle, vEgo = [], [], []
  for e in events:
    try: w = e.which()
    except Exception: continue
    t = (e.logMonoTime or 0) / 1e9
    if w == "carControl":
      try:
        cc_t.append(t); steer.append(float(e.carControl.actuators.steeringAngleDeg))
        lat.append(bool(e.carControl.latActive))
      except Exception: pass
    elif w == "carState":
      try:
        cs_t.append(t); angle.append(float(e.carState.steeringAngleDeg)); vEgo.append(float(e.carState.vEgo))
      except Exception: pass
  return (np.array(cc_t), np.array(steer), np.array(lat)), (np.array(cs_t), np.array(angle), np.array(vEgo))

def contiguous_runs(mask):
  runs = []; start = None
  for i, m in enumerate(mask):
    if m and start is None: start = i
    elif not m and start is not None: runs.append((start, i)); start = None
  if start is not None: runs.append((start, len(mask)))
  return runs

def best_lag(cmd, act, dt, max_lag=0.8):
  """lag (s, >=0) that best aligns cmd with act by delaying cmd. Returns (lag, corr)."""
  n = min(len(cmd), len(act))
  cmd = cmd[:n]; act = act[:n]
  cz = cmd - cmd.mean(); az = act - act.mean()
  if np.std(cz) < 1e-6 or np.std(az) < 1e-6: return None, None
  cz /= np.std(cz); az /= np.std(az)
  max_samp = int(max_lag / dt)
  best_d, best_c = 0, -2
  for d in range(0, max_samp + 1):
    if d == 0: c = np.corrcoef(cz, az)[0, 1]
    else: c = np.corrcoef(cz[:-d], az[d:])[0, 1]
    if c > best_c: best_c, best_d = c, d
  return best_d * dt, best_c

def main():
  path = sys.argv[1]
  out = sys.argv[2] if len(sys.argv) > 2 else None
  ev = parse(path)
  (cc_t, steer, lat), (cs_t, angle, vEgo) = extract(ev)
  print(f"carControl:{len(cc_t)}  carState:{len(cs_t)} events")
  if len(cc_t) < 100 or len(cs_t) < 100:
    print("too few events"); return
  dt = float(np.median(np.diff(cc_t)))
  angle_cc = np.interp(cc_t, cs_t, angle)
  vEgo_cc = np.interp(cc_t, cs_t, vEgo)
  eng = np.asarray(lat, dtype=bool) & (vEgo_cc > 5.0)
  print(f"dt={dt*1000:.1f}ms ({1/dt:.0f}Hz)  engaged+>5m/s: {eng.sum()}/{len(cc_t)} ({100*eng.sum()/len(cc_t):.0f}%)")
  s, a, v = steer[eng], angle_cc[eng], vEgo_cc[eng]
  print(f"engaged speed {v.min()*3.6:.0f}-{v.max()*3.6:.0f} km/h | cmdAngle std {s.std():.2f}deg | actualAngle std {a.std():.2f}deg range {a.min():.0f}..{a.max():.0f}")
  runs = [r for r in contiguous_runs(eng) if r[1] - r[0] >= 200]
  print(f"engaged runs >=4s: {len(runs)}")
  if not runs or s.std() < 1.0:
    print("\nNOT ENOUGH steering activity to measure delay (need openpilot engaged through curves/lane changes).")
    print("Do a drive with bends and openpilot ON, then re-run.")
    return
  lags = []
  for (a0, a1) in runs:
    lag, c = best_lag(steer[a0:a1], angle_cc[a0:a1], dt)
    if lag is not None and c is not None and c > 0.3:
      lags.append((lag, c, a0, a1))
  if not lags:
    print("\nno run produced a confident alignment (corr>0.3). Steering may be too gentle/straight.")
    return
  med = float(np.median([l[0] for l in lags]))
  print(f"\n=== STEER ACTUATOR DELAY ===")
  for lag, c, a0, a1 in lags:
    print(f"  run t={cc_t[a0]-cc_t[0]:.0f}s len={a1-a0}fr: delay={lag*1000:.0f}ms (corr {c:.2f})")
  print(f"\n  MEDIAN delay = {med*1000:.0f} ms  (current interface.py value: 0.30 = 300ms)")
  if out:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    lag, c, a0, a1 = max(lags, key=lambda x: x[1])
    seg = slice(a0, a1)
    t = cc_t[seg] - cc_t[0]
    cmd = steer[seg]; act = angle_cc[seg]
    d_samp = int(round(lag / dt))
    fig, ax = plt.subplots(2, 1, figsize=(12, 7), sharex=True)
    ax[0].plot(t, cmd, label="commanded angle (carControl.actuators.steeringAngleDeg)")
    ax[0].plot(t, (act - act.mean()) / (act.std() if act.std() else 1) * (cmd.std() or 1), label="actual angle (z-scaled)", alpha=.7)
    ax[0].legend(loc="upper right"); ax[0].set_title(f"steer cmd vs actual angle — measured delay {lag*1000:.0f}ms (corr {c:.2f})")
    ax[1].plot(t[d_samp:], cmd[:-d_samp] if d_samp else cmd, label="commanded, shifted forward by measured delay")
    ax[1].plot(t, (act - act.mean()) / (act.std() if act.std() else 1) * (cmd.std() or 1), label="actual angle (z-scaled)", alpha=.7)
    ax[1].legend(loc="upper right"); ax[1].set_xlabel("time (s)")
    fig.tight_layout(); fig.savefig(out, dpi=110)
    print(f"plot saved: {out}")

if __name__ == "__main__":
  main()
