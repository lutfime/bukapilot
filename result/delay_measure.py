#!/usr/bin/env python3
"""Measure steer + longitudinal actuator delay across the 7 engaged rlogs.
Steer: lag of carState.steeringAngleDeg behind commanded angle (carControl.actuators.steeringAngleDeg).
Long:  lag of carState.aEgo behind commanded accel (carControl.actuators.accel), clean (no pedal override).
Method: per contiguous clean run, lag in [0,max] maximizing correlation; report median.
Current config: steerActuatorDelay=0.17, longitudinalActuatorDelay=0.45."""
import sys, glob, os, numpy as np
sys.path.insert(0, "/Users/WanLutfi/Documents/Xcode/Kommu")
from cereal import log as capnp_log
import zstandard as zstd
EV = capnp_log.Event
def parse(p):
  data=open(p,"rb").read()
  try: return list(EV.read_multiple_bytes(data))
  except: pass
  r=zstd.ZstdDecompressor().stream_reader(data); return list(EV.read_multiple_bytes(r.read()))
def runs(mask):
  out,s=[],None
  for i,m in enumerate(mask):
    if m and s is None: s=i
    elif not m and s is not None: out.append((s,i)); s=None
  if s is not None: out.append((s,len(mask)))
  return out
def best_lag(cmd,act,dt,max_lag):
  n=min(len(cmd),len(act)); cmd=cmd[:n]; act=act[:n]
  cz=cmd-cmd.mean(); az=act-act.mean()
  if cz.std()<1e-6 or az.std()<1e-6: return None,None
  cz/=cz.std(); az/=az.std()
  bd,bc=0,-2
  for d in range(0,int(max_lag/dt)+1):
    c=np.corrcoef(cz[:-d] if d else cz, az[d:] if d else az)[0,1] if d else np.corrcoef(cz,az)[0,1]
    if c is not None and c>bc: bc,bc2=c,d; bc=c
  # careful rewrite:
  bd,bc=0,-2
  for d in range(0,int(max_lag/dt)+1):
    c=np.corrcoef(cz[:-d] if d else cz, az[d:] if d else az)[0,1] if d else np.corrcoef(cz,az)[0,1]
    if c is not None and c>bc: bc=c; bd=d
  return bd*dt,bc
def smooth(x,w):
  w=max(3,int(w)); return np.convolve(x,np.ones(w)/w,mode="same")

steer_lags=[]; long_lags=[]
for p in sorted(glob.glob("result/drives/2026-08-08--1*/rlog.zst")):
  ev=parse(p); seg=os.path.basename(os.path.dirname(p))
  cc_t,sa,ac,lat,longa=[],[],[],[],[]
  cs_t,angle,aEgo,gas,brake,vE=[],[],[],[],[],[]
  for e in ev:
    try: w=e.which()
    except: continue
    t=(e.logMonoTime or 0)/1e9
    if w=="carControl":
      act=e.carControl.actuators
      cc_t.append(t); sa.append(float(getattr(act,"steeringAngleDeg",0) or 0))
      ac.append(float(getattr(act,"accel",0) or 0)); lat.append(bool(getattr(e.carControl,"latActive",False)))
      longa.append(bool(getattr(e.carControl,"longActive",False)))
    elif w=="carState":
      cs=e.carState
      cs_t.append(t); angle.append(float(getattr(cs,"steeringAngleDeg",0) or 0))
      ae=getattr(cs,"aEgo",None); aEgo.append(float(ae) if ae is not None else float('nan'))
      gas.append(bool(getattr(cs,"gasPressed",False))); brake.append(bool(getattr(cs,"brakePressed",False)))
      vE.append(float(getattr(cs,"vEgo",0) or 0))
  if len(cc_t)<300: continue
  cc_t=np.array(cc_t);sa=np.array(sa);ac=np.array(ac);lat=np.array(lat,bool);longa=np.array(longa,bool)
  cs_t=np.array(cs_t);angle=np.array(angle);aEgo=np.array(aEgo);gas=np.array(gas,bool);brake=np.array(brake,bool);vE=np.array(vE)
  ang_c=np.interp(cc_t,cs_t,angle); vE_c=np.interp(cc_t,cs_t,vE)
  ae_c=np.interp(cc_t,cs_t,aEgo); gas_c=np.interp(cc_t,cs_t,gas.astype(float))>0.5; brake_c=np.interp(cc_t,cs_t,brake.astype(float))>0.5
  dt=float(np.median(np.diff(cc_t)))
  if not np.isfinite(ae_c).mean()>0.5: ae_c=np.gradient(vE_c,cc_t)  # fallback
  # STEER
  smask=lat & (vE_c>5)
  sl=[]
  for a0,a1 in [r for r in runs(smask) if r[1]-r[0]>=200]:
    if sa[a0:a1].std()<1.0: continue
    lg,c=best_lag(sa[a0:a1],ang_c[a0:a1],dt,0.8)
    if lg is not None and c is not None and c>0.3: sl.append((lg,c))
  # LONG
  cmd_s=smooth(ac,max(3,int(0.1/dt))); act_s=smooth(ae_c,max(3,int(0.1/dt)))
  lmask=longa & (vE_c>2) & (~gas_c) & (~brake_c) & np.isfinite(ac) & np.isfinite(ae_c)
  ll=[]
  min_len=max(20,int(2.0/dt))
  for a0,a1 in [r for r in runs(lmask) if r[1]-r[0]>=min_len]:
    if ac[a0:a1].std()<0.3: continue
    lg,c=best_lag(cmd_s[a0:a1],act_s[a0:a1],dt,1.2)
    if lg is not None and c is not None and c>0.3: ll.append((lg,c))
  s_med=np.median([x[0] for x in sl])*1000 if sl else float('nan')
  l_med=np.median([x[0] for x in ll])*1000 if ll else float('nan')
  steer_lags.extend([(seg,x[0],x[1]) for x in sl]); long_lags.extend([(seg,x[0],x[1]) for x in ll])
  print(f"{seg}: steer {len(sl):2d} runs, median={s_med:4.0f}ms | long {len(ll):2d} runs, median={l_med:4.0f}ms")

print("\n================ SUMMARY ================")
if steer_lags:
  sv=np.array([x[1] for x in steer_lags])*1000
  print(f"STEER delay:   n={len(sv)} median={np.median(sv):.0f}ms  p25={np.percentile(sv,25):.0f}  p75={np.percentile(sv,75):.0f}  (config steerActuatorDelay = 0.17 = 170ms)")
else: print("STEER: no confident runs")
if long_lags:
  lv=np.array([x[1] for x in long_lags])*1000
  print(f"LONG  delay:   n={len(lv)} median={np.median(lv):.0f}ms  p25={np.percentile(lv,25):.0f}  p75={np.percentile(lv,75):.0f}  (config longitudinalActuatorDelay = 0.45 = 450ms)")
else: print("LONG: no confident runs (need clean no-pedal cruise)")
