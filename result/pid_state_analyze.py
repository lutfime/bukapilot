#!/usr/bin/env python3
"""PID (lateral) drive analysis for Proton X70 — reads pidState from rlog(.zst).

Designed for the KA1-tuned PID lateral controller (NOT torque). The pidState
schema gives us: p, i, f, output, angleError, steeringAngleDesiredDeg,
steeringAngleDeg, steeringRateDeg, active, saturated.

Usage:
  python3 pid_state_analyze.py <rlog-or-rlog.zst> [<rlog> ...]
  python3 pid_state_analyze.py result/drives/2026-08-09--05-45-16--*.rlog.zst

Reports per-drive and pooled-by-speed:
  1. WOBBLE / JERK  — output step/tick, sign-flip rate, residual std (the main
     "is it smooth?" metric). Targets: <1.5 flips/s, output step p95 < 0.05.
  2. PID TERM BALANCE — rms of p / i / f vs OUT. Tells whether P is over-working
     (ff too low) or I is doing too much (wind-up / reengage slam).
  3. TRACKING ERROR — |angleError| by speed. Healthy ~0.5-1.5 deg at highway.
  4. SATURATION — how often output rails at ±steer_max (= under-powered).
  5. FEEDFORWARD FIT — regress OUT on (angle_des * vEgo^2) to estimate the
     effective steady-state gain the closed loop is synthesizing, and compare
     to the configured kf. Big eff/kf ratio => ff is under-tuned and P+I carry
     the load (rougher, slower response).

The script auto-detects compression (.zst) and the cereal module location.
"""
import sys, os, glob, numpy as np

# --- locate cereal (this checkout takes priority) ---
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
  sys.path.insert(0, _ROOT)
from cereal import log as capnp_log  # noqa: E402

EV = capnp_log.Event
nan = float("nan")

import zstandard as zstd  # noqa: E402


# ---- current X70 PID gains (keep in sync with opendbc/.../proton/interface.py) ----
KP_BP = [0.0, 5.0, 15.0, 25.0, 35.0]
KP_V  = [0.0005, 0.02, 0.05, 0.10, 0.17]
KI_BP = [0.0, 5.0, 15.0, 25.0, 35.0]
KI_V  = [0.001, 0.01, 0.09, 0.4, 0.5]
KF    = 0.0000250
STEER_MAX = 580.0

# speed buckets for pooled stats (m/s)
BUCK = [(0, 5), (5, 10), (10, 15), (15, 20), (20, 25), (25, 35), (35, 99)]
BN   = [f"{a}-{b}" for a, b in BUCK]


def parse(path):
  with open(path, "rb") as f:
    data = f.read()
  try:
    return list(EV.read_multiple_bytes(data))
  except Exception:
    pass
  try:
    return list(EV.read_multiple_bytes(zstd.ZstdDecompressor().decompress(data)))
  except Exception:
    r = zstd.ZstdDecompressor().stream_reader(data)
    return list(EV.read_multiple_bytes(r.read()))


def G(o, *p):
  """safe getattr chain; None on any miss."""
  c = o
  for x in p:
    try:
      c = getattr(c, x)
    except Exception:
      return None
  return c


def pct(x, q):
  x = np.asarray(x)
  x = x[np.isfinite(x)]
  return float(np.percentile(x, q)) if len(x) else nan


def rms(x):
  x = np.asarray(x)
  return float(np.sqrt(np.mean(x ** 2))) if len(x) else 0.0


def bidx(v):
  for i, (a, b) in enumerate(BUCK):
    if a <= v < b:
      return i
  return None


def zcrate(r, dt):
  if len(r) < 3:
    return 0.0
  s = np.sign(r)
  s[s == 0] = 1
  return float(np.sum(np.abs(np.diff(s)) > 0) / (len(r) * dt))


def movavg(x, w):
  w = max(3, int(w))
  return np.convolve(x, np.ones(w) / w, mode="same")


def interp(t_new, t_old, v_old):
  if len(t_old) > 1:
    return np.interp(t_new, t_old, v_old)
  return np.full_like(t_new, v_old[0] if len(v_old) else 0.0, dtype=float)


def analyze_segment(path):
  """Return dict of arrays for one segment, or None if not enough pidState data."""
  ev = parse(path)
  t = []; out = []; P = []; I = []; F = []; ae = []; ades = []
  sat = []; active = []
  tcs = []; cs_curv = []; cs_curv_des = []
  tcc = []; cc_torque = []; cc_lat = []
  ts = []; vEgo = []; steer = []; srate = []; aEgo = []
  for e in ev:
    try:
      w = e.which()
    except Exception:
      continue
    if w == "controlsState":
      ps = G(e.controlsState, "lateralControlState", "pidState")
      if ps is not None:
        t.append(e.logMonoTime)
        out.append(G(ps, "output") or 0.0)
        P.append(G(ps, "p") or 0.0)
        I.append(G(ps, "i") or 0.0)
        F.append(G(ps, "f") or 0.0)
        ae.append(G(ps, "angleError") or 0.0)
        ades.append(G(ps, "steeringAngleDesiredDeg") or 0.0)
        sat.append(1 if G(ps, "saturated") else 0)
        active.append(1 if G(ps, "active") else 0)
      # desired curvature (path intent) for corner detection
      dc = G(e.controlsState, "desiredCurvature")
      if dc is not None:
        tcs.append(e.logMonoTime); cs_curv.append(float(dc))
    elif w == "carState":
      ts.append(e.logMonoTime)
      vEgo.append(G(e.carState, "vEgo") or 0.0)
      steer.append(G(e.carState, "steeringAngleDeg") or 0.0)
      sr = G(e.carState, "steeringRateDeg"); srate.append(abs(sr or 0.0))
      ae_ = G(e.carState, "aEgo"); aEgo.append(ae_ if ae_ is not None else nan)
    elif w == "carControl":
      tcc.append(e.logMonoTime)
      act = G(e.carControl, "actuators")
      cc_torque.append(G(act, "torque") or 0.0)
      cc_lat.append(1 if G(e.carControl, "latActive") else 0)

  if len(t) < 50:
    return None

  return dict(
    seg=os.path.basename(path),
    t=np.array(t, float), out=np.array(out), p=np.array(P), i=np.array(I),
    f=np.array(F), ae=np.array(ae), ades=np.array(ades),
    sat=np.array(sat, bool), active=np.array(active, bool),
    tcs=np.array(tcs, float), cs_curv=np.array(cs_curv),
    ts=np.array(ts, float), vEgo=np.array(vEgo), steer=np.array(steer),
    srate=np.array(srate), aEgo=np.array(aEgo),
    tcc=np.array(tcc, float), cc_torque=np.array(cc_torque),
    cc_lat=np.array(cc_lat, bool),
  )


def print_segment_summary(r):
  dt = float(np.median(np.diff(r["t"])) / 1e9)
  eng = r["active"]
  vt = interp(r["t"], r["ts"], r["vEgo"])
  out = r["out"]; P = r["p"]; I = r["i"]; F = r["f"]
  flips = np.sum(np.diff(np.sign(out)) != 0) / max((r["t"][-1] - r["t"][0]) / 1e9, 1e-6)
  dout = np.abs(np.diff(out))
  print(f"\n== {r['seg']} ==")
  print(f"   {len(r['t'])} frames @ {1/dt:.0f}Hz  active={100*eng.mean():.0f}%  "
        f"vEgo mean={np.mean(vt):.1f} p50={pct(vt,50):.1f} max={np.max(vt):.1f} m/s")
  print(f"   rms: p={rms(P):.4f} i={rms(I):.4f} f={rms(F):.4f} OUT={rms(out):.4f}"
        f"  | f/OUT={100*rms(F)/max(rms(out),1e-9):.0f}%  i/OUT={100*rms(I)/max(rms(out),1e-9):.0f}%")
  print(f"   OUT step/tick: mean={np.mean(dout):.4f} p95={pct(dout,95):.4f} max={np.max(dout):.4f}"
        f"  sign-flips/s={flips:.2f}")
  print(f"   |angleError|: mean={np.mean(np.abs(r['ae'])):.2f}° p95={pct(np.abs(r['ae']),95):.2f}°"
        f"  saturated={100*r['sat'].mean():.1f}%")


def pooled_jitter(segments):
  """Per-speed-bucket output-step and flip-rate across all segments."""
  print("\n========== OUTPUT step/tick BY SPEED — THE JERK METRIC ==========")
  print(f"  {'speed(m/s)':<12}{'n':>8}{'mean':>9}{'p50':>9}{'p95':>9}{'max':>9}{'flips/s':>9}")
  all_d = []; all_v = []
  for r in segments:
    if len(r["t"]) < 2:
      continue
    vt = interp(r["t"][:-1], r["ts"], r["vEgo"])
    d = np.abs(np.diff(r["out"]))
    all_d.append(d); all_v.append(vt)
  if not all_d:
    print("  (no data)"); return
  d = np.concatenate(all_d); v = np.concatenate(all_v)
  for (a, b), name in zip(BUCK, BN):
    m = (v >= a) & (v < b)
    if m.sum() < 5:
      continue
    # flips/s in-bucket: use sign changes among consecutive in-bucket samples (approx)
    print(f"  {name:<12}{m.sum():>8}{np.mean(d[m]):>9.4f}{pct(d[m],50):>9.4f}"
          f"{pct(d[m],95):>9.4f}{np.max(d[m]):>9.4f}")
  print(f"  {'ALL':<12}{len(d):>8}{np.mean(d):>9.4f}{pct(d,50):>9.4f}"
        f"{pct(d,95):>9.4f}{np.max(d):>9.4f}")
  print("  (target: p95 < 0.05; lower = smoother steering wheel)")


def pooled_balance(segments):
  print("\n========== PID TERM BALANCE BY SPEED (rms) ==========")
  print(f"  {'speed':<10}{'|OUT|':>8}{'|p|':>8}{'|i|':>8}{'|f|':>8}"
        f"{'f/OUT%':>8}{'i/OUT%':>8}{'sat%':>7}")
  for (a, b), name in zip(BUCK, BN):
    oS = pS = iS = fS = sS = n = 0.0; cnt = 0
    for r in segments:
      vt = interp(r["t"], r["ts"], r["vEgo"])
      m = r["active"] & (vt >= a) & (vt < b)
      if m.sum() < 5:
        continue
      oS += np.sum(r["out"][m] ** 2); pS += np.sum(r["p"][m] ** 2)
      iS += np.sum(r["i"][m] ** 2); fS += np.sum(r["f"][m] ** 2)
      sS += float(r["sat"][m].sum()); n += m.sum(); cnt += 1
    if n < 5:
      continue
    OUT = np.sqrt(oS / n); P = np.sqrt(pS / n); I = np.sqrt(iS / n); F = np.sqrt(fS / n)
    print(f"  {name:<10}{OUT:>8.4f}{P:>8.4f}{I:>8.4f}{F:>8.4f}"
          f"{100*F/max(OUT,1e-9):>7.0f}%{100*I/max(OUT,1e-9):>7.0f}%{100*sS/n:>6.1f}%")
  print("  (healthy: f carries 50-80% of OUT at highway; i small; sat < 5%)")


def pooled_tracking(segments):
  print("\n========== TRACKING: |angleError| (deg) BY SPEED ==========")
  print(f"  {'speed':<10}{'mean':>8}{'p50':>8}{'p90':>8}{'p95':>8}{'max':>8}")
  for (a, b), name in zip(BUCK, BN):
    e_all = []
    for r in segments:
      vt = interp(r["t"], r["ts"], r["vEgo"])
      m = r["active"] & (vt >= a) & (vt < b)
      e_all.append(np.abs(r["ae"][m]))
    if not e_all:
      continue
    e = np.concatenate(e_all)
    if len(e) < 5:
      continue
    print(f"  {name:<10}{np.mean(e):>8.2f}{pct(e,50):>8.2f}{pct(e,90):>8.2f}"
          f"{pct(e,95):>8.2f}{np.max(e):>8.2f}")
  print("  (target highway: mean < 1.5°, p95 < 4°; bigger = lazy/loose)")


def feedforward_fit(segments):
  print("\n========== FEEDFORWARD FIT (kf adequacy) ==========")
  print(f"  configured kf = {KF:.2e}   (f = kf * angle_des * vEgo^2)")
  X = []; Y = []; F = []
  for r in segments:
    if len(r["t"]) < 10:
      continue
    vt = interp(r["t"], r["ts"], r["vEgo"])
    m = r["active"] & (vt >= 5) & np.isfinite(r["ades"])
    x = r["ades"][m] * vt[m] ** 2
    X.extend(x); Y.extend(np.abs(r["out"][m])); F.extend(np.abs(r["f"][m]))
  X = np.array(X); Y = np.array(Y); F = np.array(F)
  if len(X) < 30 or np.sum(X ** 2) == 0:
    print("  (insufficient engaged highway data)"); return
  eff = float(np.sum(X * Y) / np.sum(X ** 2))   # |OUT| ~ eff * basis
  eff_f = float(np.sum(X * F) / np.sum(X ** 2)) if np.sum(X ** 2) else 0.0
  print(f"  effective closed-loop gain on (angle_des·vEgo²): eff = {eff:.3e}")
  print(f"  configured kf                                  = {KF:.3e}")
  print(f"  ratio eff/kf = {eff/KF:.1f}×  -> P+I are synthesizing ~{eff/KF:.0f}× the open-loop feedforward")
  print(f"  (measured f-gain vs configured: {eff_f:.3e} = {eff_f/KF:.1f}× kf)")
  if eff / KF > 5:
    print(f"  => ff is UNDER-tuned. Conservative first step: kf ~ {3*KF:.2e}-{5*KF:.2e} (3-5×), then iterate.")
  elif eff / KF > 2:
    print(f"  => ff somewhat low. Try kf ~ {2*KF:.2e}-{3*KF:.2e} (2-3×).")
  else:
    print(f"  => ff is in the right ballpark; no kf change needed.")


def corner_events(segments):
  """Top lateral-accel moments: did openpilot hold tracking, or wobble?"""
  print("\n========== CORNER EVENTS (highest lateral-accel, tracking quality) ==========")
  # use actual curvature from steer angle if desiredCurvature sparse
  found = 0
  for r in segments:
    if len(r["t"]) < 50:
      continue
    vt = interp(r["t"], r["ts"], r["vEgo"])
    # lateral accel from desired curvature (intent) — falls back to steer-based
    if len(r["tcs"]) > 1 and len(r["cs_curv"]) > 1:
      dc = interp(r["t"], r["tcs"], r["cs_curv"])
    else:
      dc = np.zeros_like(r["t"])
    latacc_intent = np.abs(dc) * vt ** 2
    m = r["active"] & (vt > 8) & (latacc_intent > 1.5)
    idx = np.where(m)[0]
    # dedupe (1s apart)
    peaks = []; last = -9999
    for i in idx:
      if i - last > 100:
        peaks.append(i); last = i
    peaks = sorted(peaks, key=lambda i: -latacc_intent[i])[:4]
    for i in sorted(peaks):
      sl = slice(max(0, i - 30), min(len(r["t"]), i + 30))
      tr = (r["t"][i] - r["t"][0]) / 1e9
      print(f"  t={tr:6.0f}s v={vt[i]*3.6:4.0f}km/h lataccIntent={latacc_intent[i]:.1f} "
            f"|angleErr|={abs(r['ae'][i]):.2f}° OUT={r['out'][i]:+.3f} "
            f"sat={int(r['sat'][i])} steer={r['steer'][i]:+.0f}°")
      found += 1
  if not found:
    print("  (no high-lateral-accel engaged moments > 8 m/s found)")


def main():
  paths = []
  for a in sys.argv[1:]:
    paths.extend(sorted(glob.glob(a)))
  paths = sorted(paths, key=lambda p: (p, int(os.path.basename(p).rsplit("--", 1)[-1].split(".")[0])))
  if not paths:
    print("usage: pid_state_analyze.py <rlog-or-rlog.zst> [<rlog> ...]")
    print("       (globs OK, e.g. result/drives/2026-08-09--05-45-16--*.rlog.zst)")
    sys.exit(1)

  print(f"PID (lateral) analysis — X70. {len(paths)} segment(s).")
  print(f"gains: kpV={KP_V} @ {KP_BP}  kiV={KI_V} @ {KI_BP}  kf={KF:.2e}  STEER_MAX={STEER_MAX:.0f}\n")

  segments = []
  for p in paths:
    r = analyze_segment(p)
    if r is None:
      print(f"  skip {os.path.basename(p)} (no pidState data)");
      continue
    segments.append(r)
    print_segment_summary(r)

  if not segments:
    print("\nNo usable pidState segments found."); return

  pooled_jitter(segments)
  pooled_balance(segments)
  pooled_tracking(segments)
  feedforward_fit(segments)
  corner_events(segments)
  print("\n(done)")


if __name__ == "__main__":
  main()
