#!/usr/bin/env python3
"""Measure longitudinal actuator delay from a saved log (like steer_delay.py).

Delay = lag between commanded accel (carControl.actuators.accel) and actual accel
(carState.aEgo), on CLEAN frames: moving, openpilot engaged, NO driver gas/brake
override. Driver pedal input must be excluded or it scrambles the correlation.
"""
import sys, numpy as np
sys.path.insert(0, "/Users/WanLutfi/Documents/Xcode/Kommu Lutfi")
from cereal import log as capnp_log
EV = capnp_log.Event

def parse(path):
  with open(path, "rb") as f: data = f.read()
  try: return list(EV.read_multiple_bytes(data))
  except Exception: pass
  import zstandard as zstd
  try: return list(EV.read_multiple_bytes(zstd.ZstdDecompressor().decompress(data)))
  except Exception:
    r = zstd.ZstdDecompressor().stream_reader(data); return list(EV.read_multiple_bytes(r.read()))
def safe(o, *p, default=float("nan")):
  cur = o
  for x in p:
    try: cur = getattr(cur, x)
    except Exception: return default
  return cur
def runs(mask):
  out, s = [], None
  for i, m in enumerate(mask):
    if m and s is None: s = i
    elif not m and s is not None: out.append((s, i)); s = None
  if s is not None: out.append((s, len(mask)))
  return out
def best_lag(cmd, act, dt, max_lag=1.2):
  n = min(len(cmd), len(act)); cmd = cmd[:n]; act = act[:n]
  cz = cmd - cmd.mean(); az = act - act.mean()
  if cz.std() < 1e-6 or az.std() < 1e-6: return None, None
  cz /= cz.std(); az /= az.std()
  best_d, best_c = 0, -2
  for d in range(0, int(max_lag/dt) + 1):
    c = np.corrcoef(cz[:-d] if d else cz, az[d:] if d else az)[0,1] if d else np.corrcoef(cz, az)[0,1]
    if c is not None and c > best_c: best_c, best_d = c, d
  return best_d*dt, best_c
def smooth(x, w):
  w = max(3, int(w)); return np.convolve(x, np.ones(w)/w, mode="same")

def main():
  path = sys.argv[1]
  ev = parse(path)
  cc_t, accel, lat = [], [], []
  cs_t, aEgo, gas, brake, vEgo = [], [], [], [], []
  for e in ev:
    try: w = e.which()
    except Exception: continue
    t = (e.logMonoTime or 0)/1e9
    if w == "carControl":
      cc_t.append(t); accel.append(float(safe(e.carControl.actuators, "accel"))); lat.append(bool(safe(e.carControl, "latActive")))
    elif w == "carState":
      cs = e.carState
      cs_t.append(t); aEgo.append(float(safe(cs, "aEgo")))
      gas.append(bool(safe(cs, "gasPressed"))); brake.append(bool(safe(cs, "brakePressed"))); vEgo.append(float(safe(cs, "vEgo")))
  cc_t=np.array(cc_t); accel=np.array(accel); lat=np.array(lat,dtype=bool)
  cs_t=np.array(cs_t); aEgo=np.array(aEgo); gas=np.array(gas,dtype=bool); brake=np.array(brake,dtype=bool); vEgo=np.array(vEgo)
  if len(cc_t) < 200: print("too few frames"); return
  aEgo_c = np.interp(cc_t, cs_t, aEgo)
  gas_c = np.interp(cc_t, cs_t, gas.astype(float)) > 0.5
  brake_c = np.interp(cc_t, cs_t, brake.astype(float)) > 0.5
  vEgo_c = np.interp(cc_t, cs_t, vEgo)
  dt = float(np.median(np.diff(cc_t)))
  has_aEgo = np.isfinite(aEgo).mean() > 0.5
  if not has_aEgo: aEgo_c = np.gradient(vEgo_c, cc_t)  # fallback

  # CLEAN: moving, engaged, NO pedal override
  clean = (vEgo_c > 2.0) & lat & (~gas_c) & (~brake_c) & np.isfinite(accel) & np.isfinite(aEgo_c)
  print(f"file: {path}")
  print(f"frames:{len(cc_t)} dt={dt*1000:.0f}ms  aEgo={'carState' if has_aEgo else 'derived'}")
  print(f"clean (moving+engaged+no pedal): {clean.sum()}/{len(cc_t)} ({100*clean.sum()/len(cc_t):.0f}%)")

  cmd_s = smooth(accel, max(3, int(0.1/dt)))
  act_s = smooth(aEgo_c, max(3, int(0.1/dt)))
  lags = []
  min_len = max(20, int(2.0/dt))  # >=2s runs
  for a0, a1 in runs(clean):
    if a1 - a0 < min_len: continue
    if accel[a0:a1].std() < 0.3: continue  # need accel activity
    lag, c = best_lag(cmd_s[a0:a1], act_s[a0:a1], dt)
    if lag is not None and c is not None and c > 0.3:
      lags.append((lag, c, a0, a1-a0))
  if not lags:
    print("\nNo clean accel-active runs with confident correlation (corr>0.3).")
    print("Data too overridden/sparse to measure longitudinal delay reliably.")
    print("Need a clean cruise drive (openpilot on, no pedal, clear road, a few min).")
    return
  med = float(np.median([l[0] for l in lags]))
  print(f"\n=== LONGITUDINAL ACTUATOR DELAY (clean, no-override runs) ===")
  for lag, c, a0, ln in lags:
    print(f"  run t={cc_t[a0]-cc_t[0]:.0f}s len={ln}fr ({ln*dt:.0f}s): delay={lag*1000:.0f}ms (corr {c:.2f})")
  print(f"\n  MEDIAN delay = {med*1000:.0f} ms   (configured longitudinalActuatorDelay = 0.45 = 450ms; release_ka2 used 0.4-0.5)")

if __name__ == "__main__":
  main()
