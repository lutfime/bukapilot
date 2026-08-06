#!/usr/bin/env python3
"""PID drive analysis for Proton X70 (route 2026-08-06--00-31-35).
Covers: (1) wobble on PID, (2) corner no-slow (longitudinal), (3) lane crossing (path).
Times reported as Malaysia local (route start 00:31:35 UTC = 08:31:35 MYT).
"""
import sys, numpy as np
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
def zcrate(r, dt):
  if len(r) < 3: return 0.0
  s = np.sign(r); s[s == 0] = 1
  return np.sum(np.abs(np.diff(s)) > 0) / (len(r)*dt)
def myt(t_rel):
  base = 8*3600 + 31*60 + 35  # 08:31:35 MYT
  s = base + int(t_rel)
  return f"{s//3600%24:02d}:{s%3600//60:02d}:{s%60:02d}"

def main():
  # sort segments by number
  paths = sorted(sys.argv[1:], key=lambda p: int(p.rsplit('--',1)[1].split('.')[0]))
  Tc=[];CMD=[];LAT=[];LONG=[]   # carControl
  Ts=[];VE=[];AE=[];ANG=[]      # carState
  Tl=[];DC=[];CU=[];DA=[];WH=[] # controlsState: desiredCurvature, curvature, desiredAccel(long), which
  Tm=[];LLP=[];PY=[]            # modelV2: laneLineProbs(4), position.y (path)
  for path in paths:
    try: ev = parse(path)
    except Exception as e: print(f"skip {path}: {e}"); continue
    for e in ev:
      try: w = e.which()
      except Exception: continue
      t = (e.logMonoTime or 0)/1e9
      if w == "carControl":
        a = e.carControl.actuators
        Tc.append(t); CMD.append(float(safe(a,"torque"))); LAT.append(bool(safe(e.carControl,"latActive",d=False)))
        LONG.append(bool(safe(e.carControl,"longActive",d=False)))
      elif w == "carState":
        s = e.carState
        Ts.append(t); VE.append(float(safe(s,"vEgo"))); AE.append(float(safe(s,"aEgo"))); ANG.append(float(safe(s,"steeringAngleDeg")))
      elif w == "controlsState":
        c = e.controlsState; lc = safe(c,"lateralControlState")
        Tl.append(t); DC.append(float(safe(c,"desiredCurvature"))); CU.append(float(safe(c,"curvature")))
        DA.append(float(safe(c,"longitudinalState","aTarget", d=float("nan")))); WH.append(str(lc.which()) if lc else "")
      elif w == "modelV2":
        m = e.modelV2
        Tm.append(t)
        LLP.append([float(x) for x in list(getattr(m,"laneLineProbs",[]))][:4])
        pos = safe(m,"position"); PY.append([float(x) for x in list(getattr(pos,"y",[]))][:33])
  if not Tc: print("no data"); return
  Tc=np.array(Tc);CMD=np.array(CMD);LAT=np.array(LAT,bool);LONG=np.array(LONG,bool)
  t0=Tc[0]
  VE=np.interp(Tc,Ts,VE);AE=np.interp(Tc,Ts,AE);ANG=np.interp(Tc,Ts,ANG)
  DC=np.interp(Tc,Tl,DC);CU=np.interp(Tc,Tl,CU);DA=np.interp(Tc,Tl,DA)
  dt=float(np.median(np.diff(Tc)))
  ctrl=max(set(WH),key=WH.count) if WH else "?"
  print(f"=== drive: {len(paths)} segs, {len(Tc)} frames @ {1/dt:.0f}Hz, controller={ctrl} ===")
  print(f"lat engaged={100*LAT.mean():.0f}%  long engaged={100*LONG.mean():.0f}%  duration={ (Tc[-1]-Tc[0]):.0f}s\n")

  # ---- 1. WOBBLE ----
  eng = LAT & np.isfinite(CMD) & (VE>5)
  resid = CMD - movavg(CMD, max(3,int(1.0/dt)))
  print("=== 1) WOBBLE (steer cmd reversals/s, 5s windows) ===")
  for lo,hi in [(0,8),(8,15),(15,25),(25,99)]:
    m=eng&(VE>=lo)&(VE<hi); ww=max(10,int(5.0/dt)); zs=[]; i=0
    while i+ww<=len(Tc):
      sl=slice(i,i+ww)
      if m[sl].mean()>0.6: zs.append(zcrate(resid[sl],dt))
      i+=ww
    if len(zs)>2: print(f"  {lo}-{hi} m/s: rev/s p50={np.median(zs):.2f} p90={np.percentile(zs,90):.2f} (n={len(zs)})")
  # desiredCurvature noise (model path) vs cmd
  def rstd(x,m):
    r=x-movavg(x,max(3,int(1.0/dt))); r=r[m&np.isfinite(r)]; return float(np.std(r)) if len(r)>10 else nan
  print(f"  residual std: steerCmd={rstd(CMD,eng):.4f}  desiredCurvature={rstd(DC,eng):.5f}  actualAngle(deg)={rstd(ANG,eng):.3f}")
  print(f"  (compare torque drive: was ~5.9 rev/s, p-term railing. PID target <1.5)\n")

  # ---- 2. CORNER EVENTS (did it slow?) ----
  print("=== 2) CORNER events (top lateral-accel moments) — did openpilot slow? ===")
  latacc = np.abs(CU)*VE**2   # actual lateral accel from measured curvature
  latacc[np.isnan(latacc)]=0
  # find local peaks in latacc (engaged)
  good = eng & (latacc>1.5) & (VE>8)  # >1.5 m/s^2 lateral = real corner
  # sample peaks by non-overlapping windows
  peaks=[]; last=-100
  for i in np.where(good)[0]:
    if i-last>100:  # ~1s apart
      peaks.append(i); last=i
  peaks=sorted(peaks,key=lambda i:-latacc[i])[:6]
  for i in sorted(peaks):
    sl=slice(max(0,i-30),min(len(Tc),i+30))  # +/-0.3s
    tr=Tc[i]-t0
    print(f"  {myt(tr)} (t={tr:.0f}s): vEgo={VE[i]*3.6:.0f}km/h  latacc={latacc[i]:.1f}m/s^2  "
          f"longAccel(aEgo)={np.mean(AE[sl]):+.2f}  desiredAccel={np.nanmean(DA[sl]):+.2f}  "
          f"|steer|={abs(CMD[i]):.2f}")
  print("  (desiredAccel near 0 / positive = NOT slowing for the corner; openpilot holds cruise unless a lead/map forces decel)\n")

  # ---- 3. LANE CROSSING (path + line prob) ----
  print("=== 3) LANE-LINE confidence drops + path deviations ===")
  if LLP:
    LLP=np.array([x+[0]*(4-len(x)) for x in LLP],float) if LLP else np.zeros((0,4))
    # interpolate to Tc timeline
    if len(Tm)==len(LLP):
      llp=np.array([np.interp(Tc,Tm,LLP[:,k]) for k in range(4)]).T
    else:
      llp=np.zeros((len(Tc),4))
    # right lane line is index 2 (0=farLeft,1=left,2=right,3=farRight) typically
    rprob=llp[:,2]; lprob=llp[:,1]
    lowright = eng & (rprob<0.4) & (VE>8)
    print(f"  right-lane-line prob <0.4 (engaged): {100*lowright.mean():.0f}% of time")
    # find drops
    drops=[]; last=-100
    for i in np.where(lowright)[0]:
      if i-last>150:
        drops.append(i); last=i
    for i in sorted(drops)[:6]:
      tr=Tc[i]-t0
      print(f"  {myt(tr)} (t={tr:.0f}s): rightLineProb={rprob[i]:.2f} leftLineProb={lprob[i]:.2f} "
            f"vEgo={VE[i]*3.6:.0f}km/h |steerCmd|={abs(CMD[i]):.2f} steerAngle={ANG[i]:+.0f}deg")
  # path y at lookahead (position.y ~ index 5-10 ~ 5-20m)
  if PY:
    PY=np.array([x+[nan]*(33-len(x)) for x in PY],float)
    if len(Tm)==len(PY):
      pathy=np.interp(Tc,Tm,PY[:,8])  # ~10m lookahead lateral path
    else:
      pathy=np.zeros(len(Tc))
    dev = np.abs(pathy)
    bigdev = eng & (dev>0.7) & np.isfinite(dev)  # path wants >0.7m lateral at 10m
    if bigdev.sum()>0:
      print(f"  desired path lateral >0.7m @10m lookahead (engaged): {100*bigdev.mean():.0f}%")
      bd=[]; last=-100
      for i in np.where(bigdev)[0]:
        if i-last>150: bd.append(i); last=i
      for i in sorted(bd)[:6]:
        tr=Tc[i]-t0
        print(f"  {myt(tr)} (t={tr:.0f}s): pathY@10m={pathy[i]:+.2f}m  steerCmd={CMD[i]:+.2f}  rLineProb={(rprob[i] if LLP.any() else nan):.2f}")
  print("\n(done)")

if __name__=="__main__": main()
