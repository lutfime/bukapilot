#!/usr/bin/env python3
"""Exact per-speed wobble breakdown for the PID drive.
Reports, for fine speed bins: engaged time, steer-cmd residual amplitude,
actual-angle residual amplitude (what you feel), reversal rate, AND the kp
that's active at that speed (from interface.py kpBP/kpV).
"""
import sys, numpy as np
sys.path.insert(0, "/Users/WanLutfi/Documents/Xcode/Kommu")
from cereal import log as capnp_log
EV = capnp_log.Event
nan=float("nan")
KPBP=[0.0,5.0,15.0,25.0,35.0]; KPV=[0.0005,0.02,0.06,0.14,0.17]  # current PID kp (interface.py)

def parse(path):
  with open(path,"rb") as f: d=f.read()
  try: return list(EV.read_multiple_bytes(d))
  except Exception: pass
  import zstandard as zstd
  try: return list(EV.read_multiple_bytes(zstd.ZstdDecompressor().decompress(d)))
  except Exception:
    r=zstd.ZstdDecompressor().stream_reader(d); return list(EV.read_multiple_bytes(r.read()))
def safe(o,*p,d=nan):
  cur=o
  for x in p:
    try: cur=getattr(cur,x)
    except Exception: return d
  return cur
def movavg(x,w): w=max(3,int(w)); return np.convolve(x,np.ones(w)/w,mode="same")
def zcrate(r,dt):
  if len(r)<3: return nan
  s=np.sign(r); s[s==0]=1; return np.sum(np.abs(np.diff(s))>0)/(len(r)*dt)

paths=sorted(sys.argv[1:], key=lambda p:int(p.rsplit('--',1)[1].split('.')[0]))
Tc=[];CMD=[];LAT=[]
Ts=[];VE=[];ANG=[]
for path in paths:
  try: ev=parse(path)
  except Exception: continue
  for e in ev:
    try: w=e.which()
    except Exception: continue
    t=(e.logMonoTime or 0)/1e9
    if w=="carControl":
      Tc.append(t); CMD.append(float(safe(e.carControl.actuators,"torque"))); LAT.append(bool(safe(e.carControl,"latActive",d=False)))
    elif w=="carState":
      Ts.append(t); VE.append(float(safe(e.carState,"vEgo"))); ANG.append(float(safe(e.carState,"steeringAngleDeg")))
Tc=np.array(Tc);CMD=np.array(CMD);LAT=np.array(LAT,bool)
VE=np.interp(Tc,Ts,VE);ANG=np.interp(Tc,Ts,ANG)
dt=float(np.median(np.diff(Tc)))
eng=LAT & np.isfinite(CMD) & (VE>5)
res_cmd=CMD-movavg(CMD,max(3,int(1.0/dt)))
res_ang=ANG-movavg(ANG,max(3,int(1.0/dt)))

print("speed bin : engaged  | cmdAmp  angleAmp  rev/s  |  kp(active)")
print("   (m/s)   :   sec    | (norm)   (deg)   (1/s)  |  (interp)")
bins=[(0,5),(5,8),(8,12),(12,16),(16,20),(20,25),(25,40)]
for lo,hi in bins:
  m=eng&(VE>=lo)&(VE<hi)
  sec=m.sum()*dt
  if sec<2:
    print(f"  {lo:2d}-{hi:<3d} : {sec:6.1f}s | (too little engaged data)")
    continue
  ca=res_cmd[m]; aa=res_ang[m]
  cmdamp=float(np.std(ca)) if len(ca)>10 else nan
  angamp=float(np.std(aa)) if len(aa)>10 else nan
  rev=zcrate(ca,dt)
  kp=np.interp((lo+hi)/2,KPBP,KPV)
  print(f"  {lo:2d}-{hi:<3d} : {sec:6.1f}s | {cmdamp:.4f}  {angamp:6.3f}   {rev:5.2f} |  {kp:.4f}")
print(f"\nkp schedule (interface.py): kpBP={KPBP} kpV={KPV}")
print("drive engaged speed distribution above shows WHERE the car actually was + wobble amplitude there.")
