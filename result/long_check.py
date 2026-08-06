#!/usr/bin/env python3
"""Longitudinal analysis: speed tracking, accel smoothness/jerk, cmd-vs-actual lag,
lead-following. Reports whether the long PID oscillates (surge/brake hunting) or tracks cleanly.
"""
import sys, numpy as np
sys.path.insert(0, "/Users/WanLutfi/Documents/Xcode/Kommu")
from cereal import log as capnp_log
EV = capnp_log.Event
nan=float("nan")

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

paths=sorted(sys.argv[1:], key=lambda p:int(p.rsplit('--',1)[1].split('.')[0]))
Tc=[];ACCL=[];LONG=[]
Ts=[];VE=[];AE=[];VC=[]
Tr=[];DREL=[];VREL=[];LSTAT=[]
for path in paths:
  try: ev=parse(path)
  except Exception: continue
  for e in ev:
    try: w=e.which()
    except Exception: continue
    t=(e.logMonoTime or 0)/1e9
    if w=="carControl":
      a=e.carControl.actuators
      Tc.append(t); ACCL.append(float(safe(a,"accel"))); LONG.append(bool(safe(e.carControl,"longActive",d=False)))
    elif w=="carState":
      s=e.carState
      Ts.append(t); VE.append(float(safe(s,"vEgo"))); AE.append(float(safe(s,"aEgo")))
      vc=float(safe(s,"vCruiseCluster",d=nan))
      if vc!=vc: vc=float(safe(s,"vCruise",d=nan))
      VC.append(vc)
    elif w=="radarState":
      ld=safe(e.radarState,"leadOne")
      if ld is not None:
        Tr.append(t); DREL.append(float(safe(ld,"dRel"))); VREL.append(float(safe(ld,"vRel")))
        LSTAT.append(bool(safe(ld,"status",d=False)))
Tc=np.array(Tc);ACCL=np.array(ACCL);LONG=np.array(LONG,bool)
VE=np.interp(Tc,Ts,VE);AE=np.interp(Tc,Ts,AE);VC=np.interp(Tc,Ts,VC)
dt=float(np.median(np.diff(Tc)))
eng=LONG & np.isfinite(VE) & (VE>1)
print(f"=== longitudinal: {len(Tc)} frames @ {1/dt:.0f}Hz, long engaged={100*LONG.mean():.0f}%, span={Tc[-1]-Tc[0]:.0f}s ===\n")

m=eng&np.isfinite(VC)&(VC>1)
if m.sum()>100:
  err=VE[m]-VC[m]
  print(f"=== SPEED TRACKING (vEgo vs cruise, engaged) ===")
  print(f"  error mean={np.mean(err):+.2f} m/s ({np.mean(err)*3.6:+.1f}km/h)  std={np.std(err):.2f} m/s")
  print(f"  cruise {np.median(VC[m])*3.6:.0f}km/h, vEgo {np.median(VE[m])*3.6:.0f}km/h (median)\n")

m=eng; ae=AE[m]
ae_s=ae[np.isfinite(ae)]
if len(ae_s)>100:
  jerk=np.abs(np.diff(ae_s)/dt)
  s=np.sign(ae_s-np.mean(ae_s)); s[s==0]=1
  rev=np.sum(np.abs(np.diff(s))>0)/(len(ae_s)*dt)
  print(f"=== ACCEL SMOOTHNESS (engaged) ===")
  print(f"  aEgo: mean={np.mean(ae_s):+.2f} std={np.std(ae_s):.2f} m/s^2  range[{ae_s.min():+.2f},{ae_s.max():+.2f}]")
  print(f"  jerk |da/dt|: p50={np.percentile(jerk,50):.2f} p95={np.percentile(jerk,95):.2f} m/s^3  (>0.5 noticeable, >1.0 harsh)")
  print(f"  accel reversal (surge/brake hunting): {rev:.2f}/s  (>0.5 = hunting)")
  ac_s=ACCL[m][np.isfinite(ACCL[m])]
  if len(ac_s)>100:
    print(f"  accel cmd: mean={np.mean(ac_s):+.2f} std={np.std(ac_s):.2f}\n")

if Tr:
  Tr=np.array(Tr);DREL=np.array(DREL);VREL=np.array(VREL);LSTAT=np.array(LSTAT,bool)
  drel=np.interp(Tc,Tr,DREL); lstat=np.interp(Tc,Tr,LSTAT.astype(float))>0.5
  lead=eng&lstat&(drel<120)&(drel>0)
  print(f"=== LEAD FOLLOWING ===")
  print(f"  lead present: {100*lead.mean():.0f}% of engaged time")
  if lead.sum()>100:
    vrel=np.interp(Tc,Tr,VREL)
    print(f"  when lead: dRel med={np.median(drel[lead]):.0f}m  vRel med={np.median(vrel[lead]):+.1f}m/s")
    decel=eng&(AE<-1.0)
    print(f"  strong decel (aEgo<-1.0): {decel.sum()*dt:.1f}s ({100*decel.mean():.1f}% engaged)  max brake={np.nanmin(AE[eng]):.2f}m/s^2")
  else:
    print("  (no significant lead-following — mostly open road / cruise hold)")
  print()

print("=== VERDICT ===")
print(" smooth = jerk p95<0.5, accel rev<0.3, small speed err. harsh/hunting = needs attention.")
