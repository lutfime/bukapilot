#!/usr/bin/env python3
"""Phone-distracted false-positive check for the Kommu KA2 DM model.
_PULLS phoneProb from driverStateV2 and shows how the phone-distraction rate
changes with the threshold (current KA2 = 0.4, mici = 0.75).
Also tests whether phoneProb correlates with head yaw (user's 'face left warns' report).
"""
import sys, numpy as np
sys.path.insert(0, "/Users/WanLutfi/Documents/Xcode/Kommu")
from cereal import log as capnp_log
EV = capnp_log.Event

def parse(path):
  with open(path, "rb") as f: data = f.read()
  try: return list(EV.read_multiple_bytes(data))
  except Exception: pass
  import zstandard as zstd
  try: return list(EV.read_multiple_bytes(zstd.ZstdDecompressor().decompress(data)))
  except Exception:
    r = zstd.ZstdDecompressor().stream_reader(data); return list(EV.read_multiple_bytes(r.read()))

def num(o, d=float("nan")):
  try: return float(o)
  except Exception: return d

def main():
  paths = sys.argv[1:]
  r_ph=[]; r_yaw=[]; r_pitch=[]; r_fp=[]; l_ph=[]
  for path in paths:
    try: ev = parse(path)
    except Exception: continue
    for e in ev:
      try: w = e.which()
      except Exception: continue
      if w == "driverStateV2":
        ds = e.driverStateV2
        R = ds.rightDriverData; L = ds.leftDriverData
        fp = num(R.faceProb, 0)
        if fp > 0.5:
          fo = list(R.faceOrientation)+[0,0,0]
          r_ph.append(num(getattr(R,"phoneProb",0))); l_ph.append(num(getattr(L,"phoneProb",0)))
          r_yaw.append(float(fo[1])); r_pitch.append(float(fo[0])); r_fp.append(fp)
  r_ph=np.array(r_ph); r_yaw=np.array(r_yaw); r_pitch=np.array(r_pitch)
  if not len(r_ph): print("no data"); return
  print(f"frames (face detected): {len(r_ph)}")
  print(f"\n=== phoneProb (rightDriverData / RHD active) ===")
  for p in [50,75,90,95,99]:
    print(f"  p{p}: {np.percentile(r_ph,p):.3f}")
  print(f"  mean={r_ph.mean():.3f}  %frames phoneProb>0.4: {100*(r_ph>0.4).mean():.0f}%")
  print(f"\n=== threshold sweep: % frames that would be flagged PHONE-distracted ===")
  for th in [0.40,0.50,0.60,0.70,0.75,0.80]:
    print(f"  _PHONE_THRESH={th:.2f}  ->  {100*(r_ph>th).mean():.1f}% flagged   {'  <-- CURRENT KA2' if th==0.40 else ('  <-- mici default' if th==0.75 else '')}")
  print(f"\n=== does phoneProb track head direction? (corr with raw yaw) ===")
  print(f"  yaw mean when phoneProb>0.4: {r_yaw[r_ph>0.4].mean():+.2f}   vs phoneProb<=0.4: {r_yaw[r_ph<=0.4].mean():+.2f}")
  print(f"  Pearson corr(phoneProb, yaw) = {np.corrcoef(r_ph, r_yaw)[0,1]:+.2f}")
  print(f"  (strong negative => high phoneProb when looking one way; matches 'face direction' perception)")
  print(f"\n=== pitch (for reference; POSE-pitch distraction) ===")
  print(f"  pitch mean={r_pitch.mean():+.2f} std={r_pitch.std():.2f}")

if __name__=="__main__": main()
