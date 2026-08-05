#!/usr/bin/env python3
"""Detect steering wobble/oscillation in a saved openpilot log.

Wobble = the lateral controller "hunting": the steer torque command rapidly
alternating sign, and/or the actual steering angle wiggling, beyond what the
road/path needs. Classic PID-too-aggressive / delay-mismatch signature.

Signals (engaged + vEgo>5; full-rate rlog preferred since wobble is high-freq):
  cmd   = carControl.actuators.torque      (normalized steer command, +/-)
  angle = carState.steeringAngleDeg        (actual angle, deg)
  pid   = controlsState.lateralControlState.pidState.output

Method: detrend each signal with a ~1s moving average; the residual IS the
high-freq hunting. Report residual RMS, peak-to-peak, and zero-crossing rate
(sign reversals/s) per ~5s window of ACTIVE steering (angle std > 1deg, so
dead-straight noise is excluded). Plot the worst window.
"""
import sys, numpy as np
sys.path.insert(0, "/Users/WanLutfi/Documents/Xcode/Kommu Lutfi")
from cereal import log as capnp_log
EV = capnp_log.Event

def parse(path):
  with open(path, "rb") as f: return list(EV.read_multiple_bytes(f.read()))

def safe(o, *p, default=float("nan")):
  cur = o
  for x in p:
    try: cur = getattr(cur, x)
    except Exception: return default
  return cur

def movavg(x, w):
  w = max(3, int(w))
  return np.convolve(x, np.ones(w)/w, mode="same")

def zcrate(resid, dt):
  if len(resid) < 3: return 0.0
  s = np.sign(resid); s[s == 0] = 1
  zc = np.sum(np.abs(np.diff(s)) > 0)
  return zc / (len(resid)*dt)  # reversals/sec

def analyze(name, t, sig, eng, dt, ma_win_s=1.0, win_s=5.0, active_mask=None):
  sig = np.asarray(sig, dtype=float)
  n = len(sig); w_ma = max(3, int(ma_win_s/dt)); w_win = max(10, int(win_s/dt))
  trend = movavg(sig, w_ma)
  resid = sig - trend
  wins = []
  i = 0
  while i + w_win <= n:
    sl = slice(i, i+w_win)
    if eng[sl].mean() > 0.8:  # mostly engaged
      r = resid[sl]
      act = active_mask[sl].mean() > 0.5 if active_mask is not None else True
      if act:
        wins.append({
          "t0": t[i]-t[0], "rms": float(np.sqrt(np.mean(r**2))),
          "p2p": float(np.ptp(r)), "zcr": zcrate(r, dt),
        })
    i += w_win
  if not wins:
    print(f"  {name}: no active engaged windows"); return None
  rms = np.array([w["rms"] for w in wins]); p2p = np.array([w["p2p"] for w in wins]); zcr = np.array([w["zcr"] for w in wins])
  print(f"  {name:18s}: wobble RMS p50={np.median(rms):.4f} p95={np.percentile(rms,95):.4f} | "
        f"peak-to-peak p50={np.median(p2p):.3f} p95={np.percentile(p2p,95):.3f} | "
        f"reversals/s p50={np.median(zcr):.2f} p95={np.percentile(zcr,95):.2f}")
  worst = max(wins, key=lambda w: w["zcr"])
  print(f"     worst window: t={worst['t0']:.0f}s  reversals/s={worst['zcr']:.2f}  rms={worst['rms']:.4f}  p2p={worst['p2p']:.3f}")
  return wins, resid, trend

def main():
  path = sys.argv[1]; out = sys.argv[2] if len(sys.argv) > 2 else None
  ev = parse(path)
  cc_t, torque, lat = [], [], []
  cs_t, angle, vEgo = [], [], []
  for e in ev:
    try: w = e.which()
    except Exception: continue
    t = (e.logMonoTime or 0)/1e9
    if w == "carControl":
      a = e.carControl.actuators
      cc_t.append(t); torque.append(float(safe(a, "torque"))); lat.append(bool(safe(e.carControl, "latActive")))
    elif w == "carState":
      cs = e.carState
      cs_t.append(t); angle.append(float(safe(cs, "steeringAngleDeg"))); vEgo.append(float(safe(cs, "vEgo")))
  cc_t = np.array(cc_t); torque = np.array(torque); lat = np.array(lat, dtype=bool)
  cs_t = np.array(cs_t); angle = np.array(angle); vEgo = np.array(vEgo)
  if len(cc_t) < 200: print("too few frames"); return
  angle_cc = np.interp(cc_t, cs_t, angle)
  vEgo_cc = np.interp(cc_t, cs_t, vEgo)
  dt = float(np.median(np.diff(cc_t)))
  eng = lat & (vEgo_cc > 5.0)
  active = np.abs(movavg(angle_cc, max(3, int(1.0/dt))) - angle_cc)  # not used as mask directly
  # active steering window mask: angle is moving (curving), exclude dead-straight
  angle_deriv = np.abs(np.gradient(angle_cc, dt))
  active_mask = movavg(angle_deriv, max(3, int(2.0/dt))) > 0.5  # deg/s avg > 0.5

  print(f"file: {path}")
  print(f"frames:{len(cc_t)}  dt={dt*1000:.1f}ms ({1/dt:.0f}Hz)  engaged+vEgo>5: {100*eng.sum()/len(cc_t):.0f}%")
  print("\n=== WOBBLE (detrended ~1s residual; active steering windows only) ===")
  r_cmd = analyze("steer cmd (torque)", cc_t, torque, eng, dt, active_mask=active_mask)
  r_ang = analyze("actual angle (deg)", cc_t, angle_cc, eng, dt, active_mask=active_mask)

  # verdict
  if r_cmd:
    z = np.array([w["zcr"] for w in r_cmd[0]])
    med_zcr = float(np.median(z))
    print(f"\n>>> median command reversal rate = {med_zcr:.2f}/s")
    if med_zcr > 1.5: print(">>> WOBBLE LIKELY: command is hunting back-and-forth rapidly (>1.5 reversals/s)")
    elif med_zcr > 0.8: print(">>> mild hunting (~0.8-1.5 reversals/s) — some oscillation")
    else: print(">>> command is smooth (<0.8 reversals/s) — little high-freq hunting")

  if out and r_cmd:
    import matplotlib; matplotlib.use("Agg"); import matplotlib.pyplot as plt
    wins = r_cmd[0]; worst = max(wins, key=lambda w: w["zcr"])
    i0 = int(worst["t0"]/dt + (cc_t[0]-cc_t[0]));
    # locate window index
    idx = int(np.argmin(np.abs(cc_t - cc_t[0] - worst["t0"])))
    w_win = max(10, int(5.0/dt)); seg = slice(idx, idx+w_win)
    tt = cc_t[seg]-cc_t[0]
    fig, ax = plt.subplots(2, 1, figsize=(12, 7), sharex=True)
    ax[0].plot(tt, torque[seg], label="steer cmd (normalized torque)", lw=.8)
    ax[0].plot(tt, movavg(torque[seg], max(3,int(1.0/dt))), label="1s trend", lw=1.5)
    ax[0].set_title(f"worst wobble window @ t={worst['t0']:.0f}s  ({worst['zcr']:.2f} reversals/s)")
    ax[0].legend(loc="upper right"); ax[0].set_ylabel("command")
    ax[1].plot(tt, angle_cc[seg], label="actual steer angle (deg)", lw=.8)
    ax[1].plot(tt, movavg(angle_cc[seg], max(3,int(1.0/dt))), label="1s trend", lw=1.5)
    ax[1].legend(loc="upper right"); ax[1].set_ylabel("deg"); ax[1].set_xlabel("time (s)")
    fig.tight_layout(); fig.savefig(out, dpi=110); print(f"plot saved: {out}")

if __name__ == "__main__":
  main()
