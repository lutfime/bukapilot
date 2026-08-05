import sys, numpy as np
sys.path.insert(0, "/Users/WanLutfi/Documents/Xcode/Kommu Lutfi")
from cereal import log as capnp_log
EV = capnp_log.Event
def parse(path):
  with open(path,"rb") as f: data=f.read()
  try: return list(EV.read_multiple_bytes(data))
  except Exception: pass
  import zstandard as zstd
  try: return list(EV.read_multiple_bytes(zstd.ZstdDecompressor().decompress(data)))
  except Exception:
    r=zstd.ZstdDecompressor().stream_reader(data); return list(EV.read_multiple_bytes(r.read()))
def safe(o,*p,d=float("nan")):
  cur=o
  for x in p:
    try: cur=getattr(cur,x)
    except Exception: return d
  return cur
def main():
  path=sys.argv[1]; ev=parse(path)
  cmd=[]; ang=[]; drv=[]; ovr=0; n=0; curv=[]; lo=[]; latacc=[]
  for e in ev:
    try: w=e.which()
    except Exception: continue
    if w=="carControl":
      cmd.append(float(safe(e.carControl.actuators,"torque")))
    elif w=="carState":
      cs=e.carState; v=float(safe(cs,"vEgo"))
      if v>5:
        n+=1
        ang.append(float(safe(cs,"steeringAngleDeg")))
        drv.append(float(safe(cs,"steeringTorque")))
        if bool(safe(cs,"steeringPressed")): ovr+=1
    elif w=="controlsState":
      curv.append(float(safe(e.controlsState,"desiredCurvature")))
    elif w=="liveTorqueParameters":
      lo.append(float(safe(e.liveTorqueParameters,"latAccelOffsetFiltered",d=0)))
      latacc.append(float(safe(e.liveTorqueParameters,"latAccelFactorFiltered",d=0)))
  cmd=np.array(cmd); ang=np.array(ang); drv=np.array(drv); curv=np.array(curv)
  print(f"file: {path}")
  print(f"moving frames: {n}  | driver OVERRIDE (steeringPressed): {100*ovr/max(1,n):.0f}%")
  print(f"mean steer CMD (actuators.torque):    {np.nanmean(cmd):+.4f}  (std {np.nanstd(cmd):.4f})")
  print(f"mean actual ANGLE (deg):              {np.nanmean(ang):+.2f}  (std {np.nanstd(ang):.2f})")
  print(f"mean DRIVER torque (steeringTorque):  {np.nanmean(drv):+.1f}  (std {np.nanstd(drv):.1f})")
  print(f"mean desiredCurvature:                {np.nanmean(curv):+.3e}")
  print(f"torqued latAccelOffset (roll comp):   {np.nanmean(lo) if len(lo) else 0:+.4f}  | latAccelFactor: {np.nanmean(latacc) if len(latacc) else 0:.3f}")
  md=np.nanmean(drv); mc=np.nanmean(cmd)
  print(f"--- bias read ---")
  print(f"  driver torque mean {md:+.1f}: driver is pushing {'LEFT (correcting right-drift)' if md < -5 else 'RIGHT (correcting left-drift)' if md > 5 else 'roughly centered'}")
  print(f"  steer cmd mean {mc:+.4f}: controller commanding {'one-sided' if abs(mc)>0.05 else 'centered'}")
if __name__=="__main__": main()
