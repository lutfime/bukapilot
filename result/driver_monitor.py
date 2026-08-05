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
  from cereal.services import SERVICE_LIST
  dsvc=[s for s in SERVICE_LIST if "driver" in s.lower()]
  print("driver services:", dsvc)
  fp,fx,fy,eyes,distract=[],[],[],[],[]
  for e in ev:
    try: w=e.which()
    except Exception: continue
    if w in dsvc:
      ds=getattr(e,w)
      p=float(safe(ds,"faceProb",d=-1))
      if p>=0:
        fp.append(p)
        pos=safe(ds,"facePosition",d=None)
        if pos is not None and len(list(pos))>=2:
          fx.append(float(list(pos)[0])); fy.append(float(list(pos)[1]))
      eyes.append(bool(safe(ds,"eyesOnRoad",d=True)))
      distract.append(bool(safe(ds,"distractedDriving",d=False)))
  if not fp:
    print("no driverState face data"); return
  fp=np.array(fp); fx=np.array(fx); fy=np.array(fy)
  print(f"\nfile: {path}")
  print(f"driverState frames: {len(fp)}")
  print(f"face detected (faceProb>0.5): {100*(fp>0.5).sum()/len(fp):.0f}%   (mean faceProb {np.mean(fp):.2f})")
  if len(fx):
    print(f"facePosition X: mean={np.mean(fx):.2f} std={np.std(fx):.2f}  range [{np.min(fx):.2f}, {np.max(fx):.2f}]  (0.5=center)")
    print(f"facePosition Y: mean={np.mean(fy):.2f} std={np.std(fy):.2f}  range [{np.min(fy):.2f}, {np.max(fy):.2f}]  (0.5=center)")
    # how often face is near the frame edge (<0.1 or >0.9 = at edge)
    edge=((fx<0.15)|(fx>0.85)|(fy<0.15)|(fy>0.85)).sum()
    print(f"face near frame EDGE (x/y <0.15 or >0.85): {100*edge/len(fx):.0f}% of frames")
    cx,cy=np.mean(fx),np.mean(fy)
    print(f"  -> face center is {'OFF-CENTER' if (abs(cx-0.5)>0.15 or abs(cy-0.5)>0.15) else 'roughly centered'} (offset x={cx-0.5:+.2f} y={cy-0.5:+.2f} from center)")
  print(f"eyesOnRoad: {100*sum(eyes)/max(1,len(eyes)):.0f}%   distractedDriving: {100*sum(distract)/max(1,len(distract)):.0f}%")
if __name__=="__main__": main()
