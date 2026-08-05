#!/usr/bin/env python3
"""Diagnose driver-monitoring (front face cam) on the Proton X70 (Malaysia RHD).

Symptom: looking LEFT -> warning, looking RIGHT -> OK.  In a RHD car the attentive
direction is forward-LEFT, so this pattern = the monitor running in LHD convention.

Reads driverStateV2 + driverMonitoringState + selfdriveState from rlog(s) and reports:
  1. was isRHD True during the drive? (False => LHD mode = the bug)
  2. wheelOnRightProb, face position (centered?), poseYawOffset calibration (biased?)
  3. yaw sign when distracted vs ok (confirms left/right asymmetry)
  4. DM alert types seen
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

def lst(o, n, d=None):
  try: return [float(x) for x in list(o)][:n]
  except Exception: return d

def dd(d):  # (faceProb, posX, posY, yaw, yawstd)
  return (float(getattr(d,"faceProb",0.)),
          lst(getattr(d,"facePosition",[]),2,[float("nan")]*2),
          lst(getattr(d,"faceOrientation",[]),3,[float("nan")]*3),
          lst(getattr(d,"faceOrientationStd",[]),2,[float("nan")]*2))

def main():
  paths = sys.argv[1:]
  if not paths:
    print("usage: dm_check.py <rlog>..."); return
  # driverMonitoringState (time-aligned)
  t_dm=[], []; isRHD=[]; distract=[]; aware=[]; dtype=[]; pyo=[]; ppo=[]
  # driverStateV2
  t_ds=[]; wrp=[]; L=[]; R=[]   # L/R = list of (fp,px,py,yaw,yawstd)
  alerts={}
  for path in paths:
    try: ev = parse(path)
    except Exception as e: print(f"  (skip {path}: {e})"); continue
    for e in ev:
      try: w = e.which()
      except Exception: continue
      t = int(getattr(e, "logMonoTime", 0))
      if w == "driverStateV2":
        ds = e.driverStateV2
        wrp.append(float(getattr(ds,"wheelOnRightProb",float("nan"))))
        t_ds.append(t); L.append(dd(ds.leftDriverData)); R.append(dd(ds.rightDriverData))
      elif w == "driverMonitoringState":
        m = e.driverMonitoringState
        isRHD.append(bool(m.isRHD)); distract.append(bool(m.isDistracted))
        aware.append(float(m.awarenessStatus)); dtype.append(int(getattr(m,"distractedType",0)))
        pyo.append(float(getattr(m,"poseYawOffset",0))); ppo.append(float(getattr(m,"posePitchOffset",0)))
      elif w == "selfdriveState":
        s = e.selfdriveState
        for attr in ("alertType1","alertType2"):
          a = str(getattr(s, attr, ""))
          if a and a != "0": alerts[a] = alerts.get(a,0)+1

  if not isRHD and not L:
    print("no DM/driverStateV2 data found"); return

  print(f"=== drive: {len(paths)} segment(s) ===")
  print(f"driverStateV2 frames: {len(L)}   driverMonitoringState samples: {len(isRHD)}")
  print()
  isRHD = np.array(isRHD[:len(aware)] or [False]); distract = np.array(distract[:len(aware)] or [False])
  aware = np.array(aware); pyo = np.array(pyo); ppo=np.array(ppo); dtype=np.array(dtype[:len(aware)])
  wrp = np.array([x for x in wrp if x==x])

  print("--- RHD decision during drive ---")
  if len(isRHD):
    print(f"  isRHD True: {100*isRHD.mean():.0f}% of samples   (False = monitor ran in LHD mode)")
  if len(wrp):
    print(f"  wheelOnRightProb: mean={np.nanmean(wrp):.2f} range [{np.nanmin(wrp):.2f},{np.nanmax(wrp):.2f}]  (>0.5 => RHD)")
  print()
  print("--- calibration offsets (driver's learned 'neutral') ---")
  if len(pyo):
    print(f"  poseYawOffset:   mean={np.mean(pyo):+.3f}  std={pyo.std():.3f}  (large |val| or high std => not converged/biased)")
    print(f"  posePitchOffset: mean={np.mean(ppo):+.3f}  std={ppo.std():.3f}")
  print()
  print("--- distraction summary ---")
  if len(distract):
    print(f"  distracted: {100*distract.mean():.1f}% of samples   mean awareness={np.nanmean(aware):.2f}")
    names={0:"none",1:"pose",2:"blink",3:"phone"}
    vals,cnt=np.unique(dtype[distract] if distract.sum() else dtype[:0],return_counts=True)
    print("  distractedType counts: " + ", ".join(f"{names.get(int(v),v)}={c}" for v,c in zip(vals,cnt)))
  print()
  # correlate distraction with raw model yaw (active convention = right if isRHD else left)
  if len(L) and len(distract):
    L=np.array(L,dtype=object); R=np.array(R,dtype=object)
    rhd = bool(np.nanmean(isRHD)>0.5) if len(isRHD) else False
    active = R if rhd else L
    fp=np.array([x[0] for x in active],float); px=np.array([x[1][0] for x in active],float)
    yaw=np.array([x[2][1] for x in active],float)  # yaw = faceOrientation[1]
    fpmask=fp>0.5
    print(f"--- active convention: {'rightDriverData (RHD)' if rhd else 'leftDriverData (LHD)'} ---")
    print(f"  face detected (prob>0.5): {100*np.nanmean(fpmask):.0f}%")
    print(f"  facePosition X: mean={np.nanmean(px[fpmask]):.2f}  (0.5=center; offset => cam mount/driver side)")
    print(f"  yaw (raw model, rad): mean={np.nanmean(yaw[fpmask]):+.2f} std={np.nanstd(yaw[fpmask]):.2f}")
    # align distract to driverStateV2 by nearest timestamp if possible (here: index-proxy)
    n=min(len(distract),len(yaw))
    dy=yaw[:n][fpmask[:n]]; dd_=distract[:n][fpmask[:n]]
    if dd_.sum()>5 and (~dd_).sum()>5:
      print(f"  when DISTRACTED: yaw mean={np.nanmean(dy[dd_]):+.2f}")
      print(f"  when OK       : yaw mean={np.nanmean(dy[~dd_]):+.2f}")
      print(f"  (yaw sign tells which way the head must point to be 'OK')")
    # compare the two conventions' yaw
    Lyaw=np.array([x[2][1] for x in L],float); Ryaw=np.array([x[2][1] for x in R],float)
    Lfp=np.array([x[0] for x in L],float)>0.5
    print(f"\n  [compare] leftDriverData  yaw mean={np.nanmean(Lyaw[Lfp]):+.2f}  facePos X={np.nanmean(np.array([x[1][0] for x in L],float)[Lfp]):.2f}")
    print(f"  [compare] rightDriverData yaw mean={np.nanmean(Ryaw[Lfp]):+.2f}  facePos X={np.nanmean(np.array([x[1][0] for x in R],float)[Lfp]):.2f}")
  print()
  print("--- alert types seen (driver-related) ---")
  da={k:v for k,v in alerts.items() if "river" in k or "istract" in k.lower() or "unresponsive" in k.lower()}
  print("  " + (", ".join(f"{k}={v}" for k,v in sorted(da.items(),key=lambda x:-x[1])) or "(none)"))
  if alerts:
    print("  [all alerts] " + ", ".join(f"{k}={v}" for k,v in sorted(alerts.items(),key=lambda x:-x[1])[:15]))

if __name__=="__main__":
  main()
