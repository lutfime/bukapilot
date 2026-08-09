#!/usr/bin/env python3
"""Detailed override characterization: strict longActive gate, vEgo>7.
At each brake/gas rising edge: capture last-seen aTarget, cmd_accel, lead, curvature."""
import sys, glob, os, numpy as np; sys.path.insert(0,".")
from cereal import log as capnp_log
import zstandard as zstd
EV=capnp_log.Event
def parse(p):
  data=open(p,"rb").read()
  try: return list(EV.read_multiple_bytes(data))
  except: pass
  r=zstd.ZstdDecompressor().stream_reader(data); return list(EV.read_multiple_bytes(r.read()))
def G(o,*p):
  c=o
  for x in p:
    try:c=getattr(c,x)
    except:return None
  return c
def summ(rows,kind):
  if not rows: print(f"  {kind}: 0"); return
  v=np.array([r['v'] for r in rows]); at=np.array([r['at'] for r in rows]); ca=np.array([r['ca'] for r in rows])
  cu=np.array([r['cu'] for r in rows]); lead=np.array([r['lead'] for r in rows])
  latg=v**2*cu
  print(f"  {kind}: {len(rows)} | vEgo mean={np.mean(v):.1f} (p10={np.percentile(v,10):.0f})")
  print(f"     OP just before: aTarget mean={np.nanmean(at):.2f} (neg=braking) | cmd_accel mean={np.nanmean(ca):.2f}")
  print(f"     corner: curv mean={np.mean(cu):.4f} (>0.005=corner {np.sum(cu>0.005)}/{len(rows)}) | latacc mean={np.mean(latg):.2f} m/s²")
  print(f"     lead present: {np.sum(lead)}/{len(rows)}")
  # classify
  corner=np.sum((cu>0.005)&(lead==0)); lead_br=np.sum((lead==1)&(at<0)); resume=np.sum((at>0.1)&(lead==0))
  print(f"     -> no-lead corner: {corner} | lead+OP-braking: {lead_br} | OP-accelerating(no lead): {resume}")
for rg in ["result/drives/2026-08-08--13-44-09--*/qlog.zst","result/drives/2026-08-08--12-19-52--*/qlog.zst"]:
  segs=sorted(glob.glob(rg), key=lambda p:int(os.path.basename(os.path.dirname(p)).split("--")[-1]))
  brake=[]; gas=[]; la=False; pb=pg=False; at=ca=cu=0.0; lead=0
  for q in segs:
    seg=os.path.basename(os.path.dirname(q))
    for e in parse(q):
      w=e.which()
      if w=="carControl":
        la=bool(G(e.carControl,"longActive")); act=G(e.carControl,"actuators"); ca=G(act,"accel") if act else ca
      elif w=="longitudinalPlan": at=G(e.longitudinalPlan,"aTarget") or at
      elif w=="radarState":
        l=G(e.radarState,"leadOne"); lead=1 if (l and G(l,"status")) else 0
      elif w=="controlsState": cu=abs(G(e.controlsState,"curvature") or 0.0)
      elif w=="carState":
        cs=e.carState; v=G(cs,"vEgo") or 0; b=bool(G(cs,"brakePressed")); g=bool(G(cs,"gasPressed"))
        if la and v>7:
          if b and not pb: brake.append(dict(seg=seg,v=v,at=at,ca=ca,cu=cu,lead=lead))
          if g and not pg: gas.append(dict(seg=seg,v=v,at=at,ca=ca,cu=cu,lead=lead))
        pb=b; pg=g
  route=rg.split("/drives/")[1].split("--*")[0]
  print(f"\n===== {route} (strict longActive, vEgo>7) =====")
  summ(brake,"BRAKE override")
  summ(gas,"GAS override")
