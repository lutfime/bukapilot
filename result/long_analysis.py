#!/usr/bin/env python3
"""Longitudinal analysis: find manual overrides + poor behavior, diagnose PID/params.

Proton runs PURE FEEDFORWARD longitudinal (kpV=kiV=[0.] -> output = a_target, no feedback),
with longitudinalActuatorDelay=0.6. This measures, from a saved drive:
  - real accel lag (cmd -> aEgo) vs configured 0.6s
  - accel tracking error (cmd vs actual)
  - speed tracking (vEgo vs vCruise)
  - manual override events (brake/gas pressed) and what the controller was doing then
  - stop-and-go quality + brake pulsing at standstill
  - smoothness (accel reversals / jerk)
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

def movavg(x, w):
  w = max(3, int(w)); return np.convolve(x, np.ones(w)/w, mode="same")

def main():
  path = sys.argv[1]; out = sys.argv[2] if len(sys.argv) > 2 else None
  ev = parse(path)
  cs_t, vEgo, aEgo, gas, brake, still = [], [], [], [], [], []
  cc_t, accel, lat = [], [], []
  cst_t, vCruise = [], []
  rd_t, dRel, vRel, lead = [], [], [], []
  for e in ev:
    try: w = e.which()
    except Exception: continue
    t = (e.logMonoTime or 0)/1e9
    if w == "carState":
      cs = e.carState
      cs_t.append(t); vEgo.append(float(safe(cs, "vEgo"))); aEgo.append(float(safe(cs, "aEgo")))
      gas.append(bool(safe(cs, "gasPressed"))); brake.append(bool(safe(cs, "brakePressed")))
      still.append(bool(safe(cs, "standstill")))
    elif w == "carControl":
      cc_t.append(t); accel.append(float(safe(e.carControl.actuators, "accel"))); lat.append(bool(safe(e.carControl, "latActive")))
    elif w == "controlsState":
      cst_t.append(t); vCruise.append(float(safe(e.controlsState, "vCruise")))
    elif w == "radarState":
      lo = safe(e.radarState, "leadOne"); st = safe(lo, "status", default=0)
      rd_t.append(t); dRel.append(float(safe(lo, "dRel", default=float("nan")))); vRel.append(float(safe(lo, "vRel", default=float("nan")))); lead.append(int(st))
  cs_t=np.array(cs_t); vEgo=np.array(vEgo); aEgo=np.array(aEgo); gas=np.array(gas,dtype=bool); brake=np.array(brake,dtype=bool); still=np.array(still,dtype=bool)
  cc_t=np.array(cc_t); accel=np.array(accel); lat=np.array(lat,dtype=bool)
  has_aEgo = np.isfinite(aEgo).mean() > 0.5
  if not has_aEgo:
    aEgo = np.gradient(vEgo, cs_t)  # derive from speed
    aEgo_src = "derived from dvEgo/dt"
  else:
    aEgo_src = "carState.aEgo"
  accel_c = np.interp(cs_t, cc_t, accel) if len(cc_t) else np.full_like(cs_t, np.nan)
  vCruise_c = np.interp(cs_t, cst_t, vCruise) if len(cst_t) else np.full_like(cs_t, np.nan)
  dt = float(np.median(np.diff(cs_t))) if len(cs_t) > 1 else 0.1
  moving = vEgo > 0.5

  print(f"file: {path}")
  print(f"frames:{len(cs_t)} dt={dt*1000:.0f}ms aEgo:{aEgo_src}  moving:{moving.sum()}  standstill:{still.sum()}")
  if len(cs_t) < 100: return

  # --- real accel lag (cmd -> aEgo), on moving frames with activity ---
  good = moving & np.isfinite(accel_c) & np.isfinite(aEgo) & (vEgo > 5)
  if good.sum() > 200:
    cmd = accel_c[good]; act = aEgo[good]
    cz = cmd - cmd.mean(); az = act - act.mean()
    if cz.std() > 1e-6 and az.std() > 1e-6:
      cz/=cz.std(); az/=az.std()
      maxd = int(1.5/dt); best_d, best_c = 0, -2
      for d in range(0, maxd+1):
        c = np.corrcoef(cz[:-d] if d else cz, az[d:] if d else az)[0,1] if d else np.corrcoef(cz,az)[0,1]
        if c is not None and c > best_c: best_c, best_d = c, d
      print(f"\nREAL longitudinal lag (cmd->aEgo): {best_d*dt:.2f}s  (corr {best_c:.2f})   [configured longitudinalActuatorDelay = 0.60s]")
    # tracking error cmd vs actual
    err = (act - cmd)
    print(f"accel tracking err (aEgo - cmd): mean={np.nanmean(err):+.2f} std={np.nanstd(err):.2f} m/s^2  (moving+vEgo>5)")

  # --- speed tracking ---
  vc = vCruise_c[np.isfinite(vCruise_c) & (vCruise_c > 0) & moving]
  if len(vc) > 50:
    verr = vEgo[np.isfinite(vCruise_c) & (vCruise_c > 0) & moving] - vc
    print(f"speed tracking err (vEgo - vCruise): mean={np.nanmean(verr):+.2f} std={np.nanstd(verr):.2f} m/s ({np.nanstd(verr)*3.6:.1f} km/h)")

  # --- overrides ---
  engaged = lat  # proxy: openpilot active
  ov_brake = brake & moving
  ov_gas = gas & moving
  print(f"\nMANUAL OVERRIDES while moving: brake {ov_brake.sum()} frames ({100*ov_brake.sum()/max(1,moving.sum()):.0f}%), gas {ov_gas.sum()} frames ({100*ov_gas.sum()/max(1,moving.sum()):.0f}%)")
  # what was controller doing at brake overrides (sampled)
  if ov_brake.sum() > 0:
    bc = accel_c[ov_brake]; bve = vEgo[ov_brake]
    print(f"  at brake-override: cmd accel mean={np.nanmean(bc):+.2f} (min {np.nanmin(bc):+.2f}) m/s^2 | vEgo mean={np.nanmean(bve):.1f} m/s")
    print(f"    -> if cmd was still +/low while user braked = controller too slow to decelerate")
  if ov_gas.sum() > 0:
    gc = accel_c[ov_gas]
    print(f"  at gas-override: cmd accel mean={np.nanmean(gc):+.2f} (max {np.nanmax(gc):+.2f}) m/s^2 | vEgo mean={np.nanmean(vEgo[ov_gas]):.1f} m/s")
    print(f"    -> if cmd was low while user gassed = controller too slow to accelerate")

  # --- stop-and-go: standstill episodes + brake pulsing ---
  eps = []; s = None
  for i, v in enumerate(still):
    if v and s is None: s = i
    elif not v and s is not None: eps.append((s, i)); s = None
  if s is not None: eps.append((s, len(still)-1))
  eps = [(a,b) for a,b in eps if cs_t[b]-cs_t[a] >= 0.5]
  print(f"\nSTOP-AND-GO: {len(eps)} standstill episodes")
  pulse = []
  for a,b in eps[:8]:
    acc_win = accel_c[a:b]
    pulse.append(np.nanstd(acc_win) if len(acc_win) > 2 else 0)
    print(f"  stopped {cs_t[b]-cs_t[a]:.1f}s  accel-cmd std={pulse[-1]:.3f} {'<-- PULSING' if pulse[-1] > 0.05 else ''}")
  if pulse: print(f"  brake-pulse (accel std @ standstill) median={np.median(pulse):.3f}  (>0.05 = pulsing/rough hold)")

  # --- smoothness ---
  amoving = accel_c[moving & np.isfinite(accel_c)]
  if len(amoving) > 50:
    sgn = np.sign(amoving); sgn[sgn==0]=1
    rev = np.sum(np.abs(np.diff(sgn)) > 0)
    print(f"\nSMOOTHNESS: accel-cmd std={np.nanstd(amoving):.2f}  reversals={rev} over {len(amoving)*dt:.0f}s ({rev/(len(amoving)*dt):.2f}/s)")

  # --- lead follow ---
  if rd_t and np.isfinite(dRel).mean() > 0.3:
    dr = np.array(dRel); ld = np.array(lead)
    has_lead = np.array([d for d in dr[ld>0] if np.isfinite(d)])
    if len(has_lead) > 20:
      vEgo_r = np.interp(rd_t, cs_t, vEgo)
      gap = has_lead / np.interp(rd_t[ld>0], cs_t, vEgo)[np.isfinite(dr[ld>0])]
      print(f"\nLEAD FOLLOW: {len(has_lead)} lead frames, dRel median={np.median(has_lead):.1f}m, time-gap median={np.nanmedian(gap):.2f}s")

  # --- plot ---
  if out:
    import matplotlib; matplotlib.use("Agg"); import matplotlib.pyplot as plt
    t0 = cs_t[0]
    fig, ax = plt.subplots(3,1, figsize=(13,9), sharex=True)
    ax[0].plot(cs_t-t0, vEgo*3.6, label="vEgo km/h"); ax[0].plot(cs_t-t0, vCruise_c*3.6, label="vCruise", alpha=.6)
    ax[0].legend(loc="upper right"); ax[0].set_ylabel("speed")
    ax[1].plot(cs_t-t0, accel_c, label="cmd accel"); ax[1].plot(cs_t-t0, aEgo, label="aEgo", alpha=.7)
    ax[1].legend(loc="upper right"); ax[1].set_ylabel("accel m/s^2")
    ax[2].plot(cs_t-t0, gas.astype(int)*0.7, label="gas override")
    ax[2].plot(cs_t-t0, brake.astype(int), label="brake override")
    ax[2].legend(loc="upper right"); ax[2].set_ylabel("driver input")
    for a,b in eps: ax[0].axvspan(cs_t[a]-t0, cs_t[b]-t0, color="red", alpha=.08)
    ax[-1].set_xlabel("time (s)"); fig.tight_layout(); fig.savefig(out, dpi=100); print(f"\nplot saved: {out}")

if __name__ == "__main__":
  main()
