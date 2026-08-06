#!/usr/bin/env python3
"""Dump lateral signals (lane probs, path, steer) for a time window to locate the 8:35 lane-crossing.
usage: lane_window.py <seg.rlog.zst>... -- window is t in [START,END] s from route start.
"""
import sys, numpy as np
sys.path.insert(0, "/Users/WanLutfi/Documents/Xcode/Kommu")
from cereal import log as capnp_log
EV = capnp_log.Event
nan=float("nan")
WLO, WHI = 200, 275  # 08:34:35 .. 08:35:50 MYT

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
def myt(tr):
  s=8*3600+31*60+35+int(tr); return f"{s//3600%24:02d}:{s%3600//60:02d}:{s%60:02d}"

paths=sorted(sys.argv[1:], key=lambda p:int(p.rsplit('--',1)[1].split('.')[0]))
recs=[]  # (t_rel, vEgo, cmd, angle, dc, [4 probs], pathy10, pathy20, desire)
for path in paths:
  try: ev=parse(path)
  except Exception: continue
  # build per-msg then merge by nearest modelV2 to controlsState
  t0=None
  cc={}; cs={}; ctrl={}; mdl=[]
  for e in ev:
    try: w=e.which()
    except Exception: continue
    t=(e.logMonoTime or 0)/1e9
    if t0 is None: t0=t
    tr=t-t0
    if w=="carControl": cc[t]=(float(safe(e.carControl.actuators,"torque")), bool(safe(e.carControl,"latActive",d=False)))
    elif w=="carState": cs[t]=(float(safe(e.carState,"vEgo")), float(safe(e.carState,"steeringAngleDeg")))
    elif w=="controlsState":
      c=e.controlsState
      ctrl[t]=(float(safe(c,"desiredCurvature")), int(safe(c,"modelLength",d=0)))
    elif w=="modelV2":
      m=e.modelV2
      probs=[float(x) for x in list(getattr(m,"laneLineProbs",[]))][:4]
      py=list(getattr(safe(m,"position"),"y",[]))[:33]
      des=int(getattr(m,"laneWidth",0)) if False else 0
      mdl.append((t, probs, py, int(getattr(m,"frameId",0))))
  if not ctrl: continue
  cct=sorted(cc); cst=sorted(cs); ctrlt=sorted(ctrl); mdlt=[x[0] for x in mdl]
  import bisect
  for t in ctrlt:
    tr=t-t0
    if not (WLO<=tr<=WHI): continue
    dc=ctrl[t][0]
    cmd,lat = cc[cc_t] if (cc_t:=cct[min(bisect.bisect(cct,t)-1,len(cct)-1)]) else (nan,False)
    ve,ang = cs[cst[min(bisect.bisect(cst,t)-1,len(cst)-1)]]
    j=min(bisect.bisect(mdlt,t)-1, len(mdl)-1)
    probs=mdl[j][1] if j>=0 else [nan]*4
    py=mdl[j][2] if j>=0 else []
    py10=py[8] if len(py)>8 else nan
    py20=py[16] if len(py)>16 else nan
    recs.append((tr,ve,cmd,ang,dc,probs,py10,py20,lat))
# downsample to ~5Hz and print engaged + interesting (low prob or big path)
recs.sort()
last=-1
print(f"{'time':>8} {'vEgo':>5} {'cmd':>6} {'angle':>6} {'desCurv':>8} {'L0':>4} {'L1':>4} {'L2':>4} {'L3':>4} {'path10':>7} {'path20':>7}  eng")
for tr,ve,cmd,ang,dc,probs,py10,py20,lat in recs:
  if tr-last<0.2: continue
  last=tr
  # highlight: engaged and (right-ish prob low OR path drifting right(+))
  mark = ""
  if lat and (probs[2]<0.5 or (not np.isnan(py20) and py20>0.5)): mark=" <=="
  print(f"{myt(tr):>8} {ve*3.6:5.0f} {cmd:+6.2f} {ang:+6.0f} {dc:+8.5f} {probs[0]:4.2f} {probs[1]:4.2f} {probs[2]:4.2f} {probs[3]:4.2f} {py10:+7.2f} {py20:+7.2f}  {'Y' if lat else '.'}{mark}")
