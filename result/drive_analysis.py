#!/usr/bin/env python3
"""Proton X70 drive analysis — PID lateral + opm10v3 model health.

Usage:
  python3 result/drive_analysis.py result/drives/2026-08-14--00-45-49--*.rlog.zst   # main drive
  python3 result/drive_analysis.py result/drives/2026-08-14--01-02-21--*.rlog.zst   # car-park opm10v3
"""
import sys, glob, re, numpy as np
sys.path.insert(0, "/Users/WanLutfi/Documents/Xcode/Kommu")
from cereal import log as capnp_log
EV = capnp_log.Event
nan = float("nan")

def parse(path):
  with open(path, "rb") as f: data = f.read()
  try: return list(EV.read_multiple_bytes(data))
  except Exception: pass
  import zstandard as zstd
  try: return list(EV.read_multiple_bytes(zstd.ZstdDecompressor().decompress(data)))
  except Exception:
    r = zstd.ZstdDecompressor().stream_reader(data); return list(EV.read_multiple_bytes(r.read()))

def safe(o, *p, d=nan):
  cur = o
  for x in p:
    try: cur = getattr(cur, x)
    except Exception: return d
  return cur

def movavg(x, w): w = max(3, int(w)); return np.convolve(x, np.ones(w)/w, mode="same")

def zcrate(resid, dt):
  if len(resid) < 3: return 0.0
  s = np.sign(resid); s[s == 0] = 1
  return np.sum(np.abs(np.diff(s)) > 0) / (len(resid)*dt)

def expand(paths):
  out = []
  for p in paths:
    out += sorted(glob.glob(p)) if any(c in p for c in "*?[") else [p]
  return sorted(out, key=segnum)


def segnum(p):
  m = re.search(r'--(\d+)\.rlog\.zst$', p)
  return int(m.group(1)) if m else 0

def main():
  paths = expand(sys.argv[1:])
  # timelines
  cc_t, cc_cmd, cc_lat, cc_long = [], [], [], []
  cs_t, cs_ve, cs_ang, cs_stq, cs_brk, cs_gas = [], [], [], [], [], []
  cs_eng, cs_avail, cs_cruise = [], [], []
  m_t, m_dcurv, m_vexp = [], [], []           # modelV2
  m_posx, m_lead_drel, m_lead_vrel = [], [], []
  cs_t2, m_ok = [], []
  rs_t, rs_drel, rs_vrel, rs_status = [], [], [], []   # radarState leadOne
  onroad = []
  lc_t, lc_wh = [], []
  lc_p, lc_i, lc_f, lc_out, lc_err, lc_sat = [], [], [], [], [], []
  lc_tp, lc_ti, lc_tf, lc_tout = [], [], [], []   # torque variants (fallback)
  n_msgs = {}
  model_hz_t = []
  for path in paths:
    try: ev = parse(path)
    except Exception as e: print(f"skip {path}: {e}"); continue
    for e in ev:
      try: w = e.which()
      except Exception: continue
      n_msgs[w] = n_msgs.get(w, 0) + 1
      tt = (e.logMonoTime or 0)/1e9
      if w == "carControl":
        a = e.carControl.actuators
        cc_t.append(tt); cc_cmd.append(float(safe(a,"torque")))
        cc_lat.append(bool(safe(e.carControl,"latActive",d=False)))
        cc_long.append(bool(safe(e.carControl,"longActive",d=False)))
      elif w == "carState":
        cs = e.carState
        cs_t.append(tt); cs_ve.append(float(safe(cs,"vEgo"))); cs_ang.append(float(safe(cs,"steeringAngleDeg")))
        cs_stq.append(float(safe(cs,"steeringTorque")))
        cs_brk.append(bool(safe(cs,"brakePressed",d=False))); cs_gas.append(bool(safe(cs,"gasPressed",d=False)))
        cs_eng.append(bool(safe(cs,"cruiseState","enabled",d=False)))
        cs_avail.append(bool(safe(cs,"cruiseState","available",d=False)))
        cs_cruise.append(bool(safe(cs,"cruiseState","enabled",d=False)))
      elif w == "modelV2":
        m = e.modelV2
        m_t.append(tt); m_dcurv.append(float(safe(m,"action","desiredCurvature")))
        m_vexp.append(float(safe(m,"meta","hardBrakePredicted",d=nan)))
        _px = safe(m,"position","x")
        try: m_posx.append(float(_px[0] if hasattr(_px,'__len__') else _px))
        except Exception: m_posx.append(nan)
        # model lead (first)
        try:
          ld = m.leadsV2[0] if len(m.leadsV2) else None
          if ld is not None:
            m_lead_drel.append(float(safe(ld,"dRel"))); m_lead_vrel.append(float(safe(ld,"vRel")))
        except Exception: pass
        cs_t2.append(tt)
      elif w == "controlsState":
        c = e.controlsState
        lc = safe(c,"lateralControlState")
        lc_t.append(tt); lc_wh.append(str(lc.which()) if lc else "")
        lc_p.append(float(safe(lc,"pidState","p"))); lc_i.append(float(safe(lc,"pidState","i")))
        lc_f.append(float(safe(lc,"pidState","f"))); lc_out.append(float(safe(lc,"pidState","output")))
        lc_err.append(float(safe(lc,"pidState","error"))); lc_sat.append(bool(safe(lc,"pidState","saturated",d=False)))
        lc_tp.append(float(safe(lc,"torqueState","p"))); lc_ti.append(float(safe(lc,"torqueState","i")))
        lc_tf.append(float(safe(lc,"torqueState","f"))); lc_tout.append(float(safe(lc,"torqueState","output")))
      elif w == "radarState":
        rs = e.radarState
        ld = safe(rs,"leadOne")
        rs_t.append(tt); rs_drel.append(float(safe(ld,"dRel"))); rs_vrel.append(float(safe(ld,"vRel")))
        rs_status.append(int(safe(ld,"status")))
      elif w == "onroadEvents":
        for evx in (e.onroadEvents.events if hasattr(e.onroadEvents,'events') else []):
          onroad.append((tt, str(evx.type) if hasattr(evx,'type') else str(evx)))

  print(f"=== {len(paths)} seg(s) | messages: " +
        ", ".join(f"{k}={v}" for k,v in sorted(n_msgs.items(), key=lambda x:-x[1])[:10]) + " ===")
  if len(cc_t) < 100:
    print("too few carControl frames");
  ctrl = max(set(lc_wh), key=lc_wh.count) if lc_wh else "?"
  print(f"controller detected: {ctrl}\n")

  # ---- MODEL HEALTH ----
  if len(m_t) > 10:
    m_dt = np.diff(sorted(m_t))
    m_dt = m_dt[(m_dt>0.01)&(m_dt<2)]
    hz = 1.0/np.median(m_dt) if len(m_dt) else nan
    print("=== MODEL HEALTH ===")
    print(f"  modelV2 rate: {hz:.1f} Hz  (n={len(m_t)}, dt p50={np.median(m_dt)*1000:.0f}ms p95={np.percentile(m_dt,95)*1000:.0f}ms)")
    dcurv = np.array(m_dcurv); posx = np.array(m_posx)
    print(f"  desiredCurvature: finite={100*np.mean(np.isfinite(dcurv)):.1f}%  "
          f"|min|={np.nanmin(np.abs(dcurv)):.4f} |max|={np.nanmax(np.abs(dcurv)):.4f}")
    nfn = int(np.sum(~np.isfinite(dcurv)))
    if nfn: print(f"  !!! {nfn} non-finite desiredCurvature frames")
    print(f"  position.x: finite={100*np.mean(np.isfinite(posx)):.1f}%  range=[{np.nanmin(posx):.1f},{np.nanmax(posx):.1f}]")
    vexp = np.array(m_vexp); vexp = vexp[np.isfinite(vexp)]
    if len(vexp): print(f"  hardBrakePredicted: mean={np.mean(vexp):.3f} max={np.max(vexp):.3f}")
    # phantom-brake signature: extreme negative model-lead vRel
    if m_lead_vrel:
      lv = np.array(m_lead_vrel)
      print(f"  model lead vRel: median={np.nanmedian(lv):.2f} min={np.nanmin(lv):.2f}  "
            f"(INT8 ok if min>-8; fp16-overflow bug was ~-16)")
    print()

  # ---- STEERING / PID ----
  if len(cc_t) > 500:
    T=np.array(cc_t); CMD=np.array(cc_cmd); LAT=np.array(cc_lat,bool); LONG=np.array(cc_long,bool)
    VE=np.interp(T,cs_t,cs_ve); ANG=np.interp(T,cs_t,cs_ang); STQ=np.interp(T,cs_t,cs_stq)
    BRK=np.interp(T,cs_t,cs_brk).astype(bool); GAS=np.interp(T,cs_t,cs_gas).astype(bool)
    ENG=np.interp(T,cs_t,cs_eng).astype(bool); AVAIL=np.interp(T,cs_t,cs_avail).astype(bool)
    dt=float(np.median(np.diff(T)))
    eng = LAT & np.isfinite(CMD)
    dur = (T[-1]-T[0])/60.0
    print("=== DRIVE OVERVIEW ===")
    print(f"  duration {dur:.1f} min, {len(T)} ctrl frames @ {1/dt:.0f}Hz")
    print(f"  engaged(latActive)={100*eng.mean():.0f}%  longActive={100*LONG.mean():.0f}%")
    print(f"  vEgo: min={np.nanmin(VE):.1f} p50={np.nanmedian(VE):.1f} max={np.nanmax(VE):.1f} m/s")
    # standby (MADS): available but not cruise enabled
    standby = AVAIL & ~ENG & (VE>1)
    print(f"  MADS standby (avail & !engaged, vEgo>1): {100*standby.mean():.0f}% of time")
    print()

    # disengagements / steer releases
    rel = eng.astype(int) - np.r_[0, eng.astype(int)[:-1]]   # 1 at engage, -1 at disengage
    diseng_idx = np.where(rel==-1)[0]
    print(f"=== ENGAGEMENT EVENTS: {len(diseng_idx)} disengagements ===")
    reasons=[]
    for idx in diseng_idx[:40]:
      # was brake/gas pressed in the ~0.5s window before?
      lo=max(0,idx-int(0.5/dt));
      brk = BRK[lo:idx+1].any(); gas = GAS[lo:idx+1].any()
      stq_big = np.abs(STQ[lo:idx+1]).max() > 65
      v = VE[idx]
      tag=[]
      if brk: tag.append("BRAKE")
      if gas: tag.append("GAS")
      if (not brk and not gas) and stq_big: tag.append("STEER-TORQUE")
      if not tag: tag.append("???-no-input")
      reasons.append(" ".join(tag))
    from collections import Counter
    print("  disengage causes: " + str(dict(Counter(reasons))))
    print()

    # PID terms (if controller is pid)
    is_pid = "pid" in ctrl.lower()
    if is_pid and len(lc_t) > 500:
      P=np.interp(T,lc_t,lc_p); I=np.interp(T,lc_t,lc_i); F=np.interp(T,lc_t,lc_f)
      OUT=np.interp(T,lc_t,lc_out); ERR=np.interp(T,lc_t,lc_err); SAT=np.interp(T,lc_t,lc_sat).astype(bool)
      wma=max(3,int(1.0/dt))
      print("=== PID TERMS (engaged) ===")
      for lo,hi,lab in [(0,8,"<8"),(8,15,"8-15"),(15,25,"15-25"),(25,99,">25")]:
        m = eng & (VE>=lo) & (VE<hi)
        if m.sum()<100: continue
        # % contribution at this speed (use median magnitude)
        pp=np.nanmedian(np.abs(P[m])); ii=np.nanmedian(np.abs(I[m])); ff=np.nanmedian(np.abs(F[m])); oo=pp+ii+ff+1e-9
        print(f"  {lab:>5} m/s (n={m.sum():4d}): |out|med={np.nanmedian(np.abs(OUT[m])):.3f}  "
              f"p={100*pp/oo:.0f}% i={100*ii/oo:.0f}% f={100*ff/oo:.0f}%  satur={100*SAT[m].mean():.0f}%")
      print()

    # wobble by speed
    wwin=max(10,int(5.0/dt)); resid_cmd = CMD - movavg(CMD,max(3,int(1.0/dt)))
    print("=== STEER COMMAND wobble (reversals/s, 5s windows) ===")
    i=0; wins=[]
    while i+wwin<=len(T):
      sl=slice(i,i+wwin)
      if eng[sl].mean()>0.7:
        wins.append((VE[sl].mean(), zcrate(resid_cmd[sl],dt)))
      i+=wwin
    for lo,hi,lab in [(0,8,"<8"),(8,15,"8-15"),(15,25,"15-25"),(25,99,">25")]:
      z=np.array([z for v,z in wins if lo<=v<hi])
      if len(z)>2: print(f"  {lab:>5} m/s: rev/s p50={np.median(z):.2f} p90={np.percentile(z,90):.2f} (n={len(z)})")
    zall=np.array([z for v,z in wins]) if wins else np.array([0])
    print(f"  OVERALL rev/s p50={np.median(zall):.2f} p90={np.percentile(zall,90):.2f}  (good <~1.5)\n")

    # corner output (the soft/wobble question): high desired curvature = corner
    if is_pid and len(m_t)>100:
      DC=np.interp(T,m_t,m_dcurv)
      corn = eng & (np.abs(DC)>0.006) & np.isfinite(CMD)   # noticeable corner curvature
      if corn.sum()>50:
        print(f"=== CORNER OUTPUT (|desiredCurv|>0.006, engaged) ===")
        print(f"  n={corn.sum()}  |torque cmd|: p50={np.nanmedian(np.abs(CMD[corn])):.3f} "
              f"p90={np.percentile(np.abs(CMD[corn]),90):.3f} max={np.nanmax(np.abs(CMD[corn])):.3f} (max=1.0)")
        if ctrl=="pid":
          print(f"  f-term at corners: p50={np.nanmedian(np.abs(F[corn])):.3f} p90={np.percentile(np.abs(F[corn]),90):.3f}")
        print()

  # ---- onroad events ----
  if onroad:
    from collections import Counter
    types = Counter(t for _,t in onroad)
    print("=== ONROAD EVENTS (top) ===")
    for t,c in types.most_common(12):
      print(f"  {c:5d}  {t}")
    print()

if __name__=="__main__": main()
