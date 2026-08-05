#!/usr/bin/env python3
"""Lateral wobble diagnosis for the Proton X70 (torque controller).

User report: low-speed ALWAYS wobbles; high-speed wobbles on medium corners.
Splits the wobble into feedforward (model) vs feedback (gain) using the
LateralTorqueState PIDF terms, and bins everything by speed.
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

def zcrate(resid, dt):
  if len(resid) < 3: return 0.0
  s = np.sign(resid); s[s == 0] = 1
  return np.sum(np.abs(np.diff(s)) > 0) / (len(resid)*dt)

def main():
  paths = sys.argv[1:]
  # carControl (100Hz) base timeline
  T=[];CMD=[];SO=[];LAT=[]
  Tcs=[];VE=[];ANG=[];STQ=[]
  Tc=[];P=[];I=[];D=[];F=[];OUT=[];ERR=[];ALAT=[];DLAT=[];SAT=[];WH=[]
  for path in paths:
    try: ev = parse(path)
    except Exception as e: print(f"skip {path}: {e}"); continue
    for e in ev:
      try: w = e.which()
      except Exception: continue
      tt = (e.logMonoTime or 0)/1e9
      if w == "carControl":
        a = e.carControl.actuators
        T.append(tt); CMD.append(float(safe(a,"torque"))); SO.append(float(safe(a,"steerOutputCan")))
        LAT.append(bool(safe(e.carControl,"latActive",d=False)))
      elif w == "carState":
        cs = e.carState
        Tcs.append(tt); VE.append(float(safe(cs,"vEgo"))); ANG.append(float(safe(cs,"steeringAngleDeg"))); STQ.append(float(safe(cs,"steeringTorque")))
      elif w == "controlsState":
        c = e.controlsState
        lc = safe(c,"lateralControlState")
        Tc.append(tt)
        WH.append(str(lc.which()) if lc else "")
        P.append(float(safe(lc,"torqueState","p"))); I.append(float(safe(lc,"torqueState","i")))
        D.append(float(safe(lc,"torqueState","d"))); F.append(float(safe(lc,"torqueState","f")))
        OUT.append(float(safe(lc,"torqueState","output"))); ERR.append(float(safe(lc,"torqueState","error")))
        ALAT.append(float(safe(lc,"torqueState","actualLateralAccel"))); DLAT.append(float(safe(lc,"torqueState","desiredLateralAccel")))
        SAT.append(bool(safe(lc,"torqueState","saturated",d=False)))
  T=np.array(T);CMD=np.array(CMD);SO=np.array(SO);LAT=np.array(LAT,bool)
  if len(T) < 500: print("too few frames"); return
  VE=np.interp(T,Tcs,VE);ANG=np.interp(T,Tcs,ANG);STQ=np.interp(T,Tcs,STQ)
  P=np.interp(T,Tc,P);I=np.interp(T,Tc,I);D=np.interp(T,Tc,D);F=np.interp(T,Tc,F)
  OUT=np.interp(T,Tc,OUT);ERR=np.interp(T,Tc,ERR);ALAT=np.interp(T,Tc,ALAT);DLAT=np.interp(T,Tc,DLAT);SAT=np.interp(T,Tc,SAT)
  dt=float(np.median(np.diff(T)))
  eng = LAT & np.isfinite(CMD) & (VE>5)
  ctrl = max(set(WH), key=WH.count) if WH else "?"
  print(f"=== drive: {len(paths)} seg(s), {len(T)} frames @ {1/dt:.0f}Hz, controller={ctrl}, engaged={100*eng.mean():.0f}% ===")
  for lo,hi,lab in [(0,8,"low <8"),(8,15,"8-15"),(15,25,"15-25"),(25,99,"high >25")]:
    m = eng & (VE>=lo) & (VE<hi)
    print(f"  speed {lab} m/s: {100*m.mean():.0f}% of engaged time")
  print()

  # --- wobble: command reversal rate, binned by speed (per 5s windows) ---
  wma=max(3,int(1.0/dt)); wwin=max(10,int(5.0/dt))
  resid_cmd = CMD - movavg(CMD,wma)
  print("=== STEER COMMAND wobble (reversals/s, 5s windows) by speed ===")
  bins=[(0,8),(8,15),(15,25),(25,99)]
  rev_by={}
  i=0; wins=[]
  while i+wwin<=len(T):
    sl=slice(i,i+wwin)
    if eng[sl].mean()>0.7:
      vmean=VE[sl].mean()
      wins.append((vmean, zcrate(resid_cmd[sl],dt), float(np.sqrt(np.mean(resid_cmd[sl]**2)))))
    i+=wwin
  for lo,hi in bins:
    z=np.array([z for v,z,r in wins if lo<=v<hi])
    if len(z)>2: print(f"  {lo}-{hi} m/s: rev/s p50={np.median(z):.2f} p90={np.percentile(z,90):.2f} (n={len(z)})")
    else: print(f"  {lo}-{hi} m/s: (not enough)")
    rev_by[(lo,hi)]=z
  zall=np.array([z for v,z,r in wins])
  print(f"  OVERALL: rev/s p50={np.median(zall):.2f} p90={np.percentile(zall,90):.2f}  (target <~1.5; earlier baseline ~8)\n")

  # --- component breakdown: is wobble feedforward (f) or feedback (p)? ---
  def rstd(x,m):
    r=x-movavg(x,wma); r=r[m&np.isfinite(r)]; return float(np.std(r)) if len(r)>10 else nan
  print("=== COMPONENT breakdown (detrended ~1s residual std, engaged) ===")
  for lo,hi in bins:
    m=eng&(VE>=lo)&(VE<hi)
    if m.sum()<200: continue
    print(f"  {lo}-{hi} m/s:  cmd={rstd(CMD,m):.4f}  output={rstd(OUT,m):.4f}  "
          f"feedforward(f)={rstd(F,m):.4f}  proportional(p)={rstd(P,m):.4f}  error={rstd(ERR,m):.4f}")
  print("  (wobble in 'f' = model/feedforward-driven; in 'p' = feedback/gain-driven)\n")

  # --- low-speed deep dive ---
  mlow=eng&(VE<8)
  if mlow.sum()>200:
    print("=== LOW-SPEED (<8 m/s) deep dive ===")
    print(f"  p-term:        mean={np.nanmean(P[mlow]):+.3f}  p95={np.percentile(np.abs(P[mlow]),95):.3f}  max|p|={np.nanmax(np.abs(P[mlow])):.3f}")
    print(f"  output:        mean={np.nanmean(OUT[mlow]):+.3f}  p95|out|={np.percentile(np.abs(OUT[mlow]),95):.3f}")
    print(f"  saturated:     {100*np.nanmean(SAT[mlow]):.0f}% of low-speed frames")
    print(f"  cmd reversals: {zcrate(resid_cmd[mlow],dt):.2f}/s   (steerCmd p2p={np.ptp(CMD[mlow]):.2f})")
    print(f"  desiredLatAccel std={np.nanstd(DLAT[mlow]):.3f}  actualLatAccel std={np.nanstd(ALAT[mlow]):.3f}\n")

  # --- high-speed cornering ---
  mhi=eng&(VE>=18)
  if mhi.sum()>200:
    corn = mhi & (np.abs(DLAT)>0.6)   # ~0.06g desired lataccel = noticeable corner
    if corn.sum()>100:
      print("=== HIGH-SPEED (>18 m/s) CORNERING (|desiredLatAccel|>0.6) ===")
      print(f"  cmd reversals: {zcrate(resid_cmd[corn],dt):.2f}/s   feedforward(f) std={rstd(F,corn):.4f}  p std={rstd(P,corn):.4f}")
      print(f"  saturated: {100*np.nanmean(SAT[corn]):.0f}%   steerCmd p2p={np.ptp(CMD[corn]):.2f}\n")

  # --- actual angle tracking lag/oscillation ---
  mang=eng&np.isfinite(ANG)
  if mang.sum()>500:
    ad=ANG-movavg(ANG,wma)
    print(f"=== ACTUAL steering angle residual std (engaged): {np.std(ad[mang]):.3f} deg (high => wheel physically wiggling) ===")

if __name__=="__main__": main()
