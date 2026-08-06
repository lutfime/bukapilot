#!/usr/bin/env python3
"""Side-by-side wobble comparison of two drives (same speed bins, same metrics).
drive A vs drive B: desired-angle noise (MODEL), actual-angle noise (FELT), reversal rate.
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
def zcrate(r,dt):
  if len(r)<3: return nan
  s=np.sign(r); s[s==0]=1; return np.sum(np.abs(np.diff(s))>0)/(len(r)*dt)
import glob
def analyze(label, files):
  Tc=[];CMD=[];LAT=[]; Ts=[];VE=[];ANG=[]; Tl=[];DANG=[]
  for p in files:
    for e in parse(p):
      try: w=e.which()
      except: continue
      t=(e.logMonoTime or 0)/1e9
      if w=="carControl":
        Tc.append(t); CMD.append(float(safe(e.carControl.actuators,"torque"))); LAT.append(bool(safe(e.carControl,"latActive",d=False)))
      elif w=="carState":
        s=e.carState; Ts.append(t); VE.append(float(safe(s,"vEgo"))); ANG.append(float(safe(s,"steeringAngleDeg")))
      elif w=="controlsState":
        c=e.controlsState; pid=safe(safe(c,"lateralControlState"),"pidState")
        Tl.append(t); DANG.append(float(safe(pid,"steeringAngleDesiredDeg")))
  Tc=np.array(Tc);CMD=np.array(CMD);LAT=np.array(LAT,bool)
  VE=np.interp(Tc,Ts,VE);ANG=np.interp(Tc,Ts,ANG);DANG=np.interp(Tc,Tl,DANG)
  dt=float(np.median(np.diff(Tc))); eng=LAT&(VE>5)&np.isfinite(CMD)
  rc=CMD-mavg(CMD,max(3,int(1.0/dt))); ra=ANG-mavg(ANG,max(3,int(1.0/dt))); rd=DANG-mavg(DANG,max(3,int(1.0/dt)))
  print(f"\n{label}: engaged={100*eng.mean():.0f}%")
  print(f"  speed : desiredNoise(actual)  reversal/s")
  out={}
  for lo,hi in [(8,12),(12,16),(16,20),(20,25)]:
    m=eng&(VE>=lo)&(VE<hi)
    if m.sum()<100: print(f"  {lo}-{hi}: (too little)"); continue
    dn=np.std(rd[m][np.isfinite(rd[m])]); an=np.std(ra[m][np.isfinite(ra[m])]); rv=zcrate(rc[m],dt)
    print(f"  {lo:2d}-{hi:<3d}: {dn:.2f} ({an:.2f})      {rv:.2f}")
    out[(lo,hi)]=(dn,an,rv)
  return out

A=analyze("DRIVE A: 00-31-35 (0.10, LAT_SMOOTH 0.0, CITY)", sorted(glob.glob("result/drives/2026-08-06--00-31-35--*.rlog.zst"), key=lambda p:int(p.rsplit('--',1)[1].split('.')[0])))
B=analyze("DRIVE B: 04-12-46 (0.10, LAT_SMOOTH 0.15, HIGHWAY)", sorted(glob.glob("result/drives/2026-08-06--04-12-46--*.rlog.zst"), key=lambda p:int(p.rsplit('--',1)[1].split('.')[0])))
print("\n=== VERDICT (desiredNoise = MODEL wobble source; lower = better) ===")
for k in A:
  if k in B:
    da=A[k][0]; db=B[k][0]
    print(f"  {k[0]}-{k[1]} m/s: desiredNoise A={da:.2f}  B={db:.2f}  -> {'B lower (LAT_SMOOTH helped)' if db<da*0.85 else 'roughly same (LAT_SMOOTH did NOT clearly help)' if abs(db-da)<0.15 else 'B higher'}")
