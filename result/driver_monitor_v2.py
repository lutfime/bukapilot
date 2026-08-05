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
def lst(o,n,d=None):
  try: return [float(x) for x in list(o)][:n]
  except Exception: return d
def main():
  path=sys.argv[1]; ev=parse(path)
  # RHD -> rightDriverData
  fprob,forient,fpos=[],[],[]
  distract,aware,dtype=[],[],[]
  ppo,pyo=[],[]
  for e in ev:
    try: w=e.which()
    except: continue
    if w=="driverStateV2":
      rd=e.driverStateV2.rightDriverData
      fprob.append(float(getattr(rd,"faceProb",0)))
      forient.append(lst(getattr(rd,"faceOrientation",[]),3,[0,0,0]))
      fpos.append(lst(getattr(rd,"facePosition",[]),2,[0.5,0.5]))
    elif w=="driverMonitoringState":
      m=e.driverMonitoringState
      distract.append(bool(m.isDistracted)); aware.append(float(m.awarenessStatus))
      dtype.append(int(getattr(m,"distractedType",0)))
      ppo.append(float(getattr(m,"posePitchOffset",0))); pyo.append(float(getattr(m,"poseYawOffset",0)))
  if not fprob: print("no driverStateV2 data"); return
  fprob=np.array(fprob); forient=np.array(forient); fpos=np.array(fpos)
  distract=np.array(distract[:len(fprob)]); aware=np.array(aware[:len(fprob)])
  print(f"file: {path}  (RHD = rightDriverData)")
  print(f"frames: {len(fprob)}  face detected (faceProb>0.5): {100*(fprob>0.5).sum()/len(fprob):.0f}%  (mean faceProb {fprob.mean():.2f})")
  print(f"\nfacePosition X (0.5=center): mean={fpos[:,0].mean():.2f} range [{fpos[:,0].min():.2f},{fpos[:,0].max():.2f}]")
  print(f"facePosition Y (0.5=center): mean={fpos[:,1].mean():.2f} range [{fpos[:,1].min():.2f},{fpos[:,1].max():.2f}]")
  edge=((fpos[:,0]<0.15)|(fpos[:,0]>0.85)|(fpos[:,1]<0.15)|(fpos[:,1]>0.85)).sum()
  print(f"  face near frame EDGE: {100*edge/len(fpos):.0f}% of frames")
  print(f"\nfaceOrientation (rad): pitch mean={forient[:,0].mean():+.2f} yaw mean={forient[:,1].mean():+.2f} roll mean={forient[:,2].mean():+.2f}")
  print(f"  pitch std={forient[:,0].std():.2f} yaw std={forient[:,1].std():.2f}")
  print(f"\nDM state: distracted {100*distract.sum()/max(1,len(distract)):.0f}%  mean awareness={aware.mean():.2f}")
  print(f"  posePitchOffset={np.mean(ppo):.2f}  poseYawOffset={np.mean(pyo):.2f}  (calibration offsets)")
  # correlation: when distracted, what's the face orientation?
  if distract.sum()>10:
    dp,dy=forient[distract.astype(bool)][:,0].mean(),forient[distract.astype(bool)][:,1].mean()
    np_,ny_=forient[~distract.astype(bool)][:,0].mean(),forient[~distract.astype(bool)][:,1].mean()
    print(f"\n  when DISTRACTED: pitch={dp:+.2f} yaw={dy:+.2f}  | when OK: pitch={np_:+.2f} yaw={ny_:+.2f}")
if __name__=="__main__": main()
