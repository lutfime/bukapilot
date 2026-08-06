#!/usr/bin/env python3
"""Highway drive check: LAT_SMOOTH 0.15 effect (desired/actual angle noise vs prev drive),
wobble by speed, lagd convergence, speed distribution. 0.10 model (0.11 toggle was off).
"""
import sys, numpy as np
sys.path.insert(0, "/Users/WanLutfi/Documents/Xcode/Kommu")
from cereal import log as capnp_log
EV = capnp_log.Event
nan=float("nan")
def parse(p):
  import zstandard as zstd
  with open(p,"rb") as f: d=f.read()
  try: return list(EV.read_multiple_bytes(d))
  except:
    try: return list(EV.read_multiple_bytes(zstd.ZstdDecompressor().decompress(d)))
    except:
      r=zstd.ZstdDecompressor().stream_reader(d); return list(EV.read_multiple_bytes(r.read()))
def safe(o,*p,d=nan):
  cur=o
  for x in p:
    try: cur=getattr(cur,x)
    except: return d
  return cur
def mavg(x,w): w=max(3,int(w)); return np.convolve(x,np.ones(w)/w,mode="same")
import glob
paths=sorted(sys.argv[1:], key=lambda p:int(p.rsplit('--',1)[1].split('.')[0]))
Tc=[];CMD=[];LAT=[]
Ts=[];VE=[];ANG=[]
Tl=[];DANG=[];DC=[];CU=[]
Tr=[];LATD=[];LSTAT=[]
for p in paths:
  for e in parse(p):
    try: w=e.which()
    except: continue
    t=(e.logMonoTime or 0)/1e9
    if w=="carControl":
      Tc.append(t); CMD.append(float(safe(e.carControl.actuators,"torque"))); LAT.append(bool(safe(e.carControl,"latActive",d=False)))
    elif w=="carState":
      s=e.carState; Ts.append(t); VE.append(float(safe(s,"vEgo"))); ANG.append(float(safe(s,"steeringAngleDeg")))
    elif w=="controlsState":
      c=e.controlsState; lc=safe(c,"lateralControlState"); pid=safe(lc,"pidState")
      Tl.append(t); DANG.append(float(safe(pid,"steeringAngleDesiredDeg"))); DC.append(float(safe(c,"desiredCurvature"))); CU.append(float(safe(c,"curvature")))
    elif w=="liveDelay":
      Tr.append(t); LATD.append(float(e.liveDelay.lateralDelay)); LSTAT.append(str(e.liveDelay.status).strip())
Tc=np.array(Tc);CMD=np.array(CMD);LAT=np.array(LAT,bool)
Ts=np.array(Ts);VE=np.interp(Tc,Ts,VE);ANG=np.interp(Tc,Ts,ANG)
DANG=np.interp(Tc,Tl,DANG);DC=np.interp(Tc,Tl,DC)
dt=float(np.median(np.diff(Tc))); eng=LAT&(VE>5)&np.isfinite(CMD)
print(f"=== highway drive: {len(paths)} segs, long engaged={100*LAT.mean():.0f}%, span={Tc[-1]-Tc[0]:.0f}s ===")
print("speed distribution (engaged):")
for lo,hi in [(0,8),(8,15),(15,20),(20,25),(25,40)]:
  m=eng&(VE>=lo)&(VE<hi); print(f"  {lo}-{hi} m/s: {100*m.mean():.0f}%")
print()
# lagd
if LSTAT:
  est=sum(1 for s in LSTAT if "estimate" in s); print(f"lagd status: estimated {est}/{len(LSTAT)} samples")
  print(f"  lateralDelay mean={np.mean(LATD):.3f}s last={LATD[-1]:.3f}  ({'REAL estimate' if est>len(LATD)//2 else 'FALLBACK (seed+0.2) — lagd did NOT converge'})")
print()
print("speed : actualAng  desiredAng |  (prev drive LAT_SMOOTH=0.0 in parens)")
prev={"5-8":(1.33,2.20),"8-12":(0.85,0.73),"12-16":(1.09,0.82),"16-20":(0.87,0.54),">20":("n/a","n/a")}
res_c=CMD-movavg if False else CMD-mavg(CMD,max(3,int(1.0/dt)))
res_a=ANG-mavg(ANG,max(3,int(1.0/dt))); res_d=DANG-mavg(DANG,max(3,int(1.0/dt)))
for lo,hi,key in [(5,8,"5-8"),(8,12,"8-12"),(12,16,"12-16"),(16,20,"16-20"),(20,40,">20")]:
  m=eng&(VE>=lo)&(VE<hi)
  if m.sum()<100: print(f"  {key}: (too little)"); continue
  an=np.std(res_a[m][np.isfinite(res_a[m])]); dn=np.std(res_d[m][np.isfinite(res_d[m])])
  p=prev.get(key,("","")); print(f"  {key}: actual={an:.2f}  desired={dn:.2f}   (prev actual={p[0]} desired={p[1]})")
print()
# overall wobble reversal
res_cmd=CMD-mavg(CMD,max(3,int(1.0/dt)))
s=np.sign(res_cmd[eng]-res_cmd[eng].mean()); s[s==0]=1
print(f"overall steer-cmd reversal: {np.sum(np.abs(np.diff(s))>0)/(len(s)*dt):.2f}/s (prev ~6-7)")
print("\n=> If desired-angle noise DROPPED vs prev, LAT_SMOOTH 0.15 is working (less model noise -> less wobble).")
