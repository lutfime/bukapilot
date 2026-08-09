#!/usr/bin/env python3
"""Scan qlogs for driver overrides during longitudinal engagement.
Counts brake/gas overrides, classifies by scenario (lead present, TTC, speed, corner).
qlog ~1Hz so counts are a lower bound; use for distribution, not exact totals."""
import sys, glob, os, numpy as np
sys.path.insert(0, "/Users/WanLutfi/Documents/Xcode/Kommu")
from cereal import log as capnp_log
import zstandard as zstd
EV = capnp_log.Event
def parse(p):
  data=open(p,"rb").read()
  try: return list(EV.read_multiple_bytes(data))
  except: pass
  r=zstd.ZstdDecompressor().stream_reader(data); return list(EV.read_multiple_bytes(r.read()))
def G(o,*p):
  c=o
  for x in p:
    try: c=getattr(c,x)
    except: return None
  return c

def scan(route_glob):
  segs=sorted(glob.glob(route_glob), key=lambda p:int(os.path.basename(os.path.dirname(p)).split("--")[-1]))
  brake_ov=[]; gas_ov=[]   # each: (seg, vEgo, lead_present, dRel, vRel, ttc, cmd_accel, aTarget, curvature)
  engaged_s=0.0
  for q in segs:
    seg=os.path.basename(os.path.dirname(q))
    ev=parse(q)
    prev_brake=False; prev_gas=False
    # maintain latest lead + plan + cmd_accel by time
    lead=None; atarget=None; caccel=None; curv=0.0
    for e in ev:
      w=e.which()
      if w=="carControl":
        cc=e.carControl
        if not (G(cc,"longActive") or G(cc,"enabled")):
          prev_brake=False; prev_gas=False
          continue
      if w=="carState":
        cs=e.carState
        brake=bool(G(cs,"brakePressed")); gas=bool(G(cs,"gasPressed"))
        v=G(cs,"vEgo") or 0.0
        # detect rising edge while engaged
        if brake and not prev_brake:
          brake_ov.append((seg,v, lead is not None, (lead[0] if lead else -1),(lead[1] if lead else 0),(lead[2] if lead else 99), caccel if caccel is not None else 0, atarget if atarget is not None else 0, curv))
        if gas and not prev_gas:
          gas_ov.append((seg,v, lead is not None, (lead[0] if lead else -1),(lead[1] if lead else 0),(lead[2] if lead else 99), caccel if caccel is not None else 0, atarget if atarget is not None else 0, curv))
        prev_brake=brake; prev_gas=gas
      elif w=="radarState":
        l=G(e.radarState,"leadOne")
        if l is not None and G(l,"status"):
          dRel=G(l,"dRel") or 0; vRel=(G(l,"vLead") or 0)-(G(l,"vRel") if False else 0)
          vLead=G(l,"vLead") or 0; vEgo_lead=G(e.radarState, "vEgo") or 0
          vRel=vLead-vEgo_lead
          d=dRel if dRel>0 else 1.0
          ttc=d/max(vEgo_lead-vLead,0.5)
          lead=(dRel, vRel, ttc)
        else:
          lead=None
      elif w=="longitudinalPlan":
        atarget=G(e.longitudinalPlan,"aTarget")
      elif w=="carControl":
        act=G(e.carControl,"actuators")
        if act is not None: caccel=G(act,"accel")
      elif w=="controlsState":
        curv=abs(G(e.controlsState,"curvature") or 0.0)
  return brake_ov, gas_ov, len(segs)

if __name__=="__main__":
  for rg in ["result/drives/2026-08-08--13-44-09--*/qlog.zst","result/drives/2026-08-08--12-19-52--*/qlog.zst"]:
    if not glob.glob(rg): continue
    route=rg.split("/drives/")[1].split("--*")[0]
    brake,gas,nseg=scan(rg)
    print(f"\n===== {route}  ({nseg} segs) =====")
    print(f"  BRAKE overrides: {len(brake)}   GAS overrides: {len(gas)}")
    def summ(arr,label):
      if not arr: print(f"  {label}: none"); return
      lead=np.array([a[2] for a in arr]); v=np.array([a[1] for a in arr])
      ttc=np.array([a[5] for a in arr]); d=np.array([a[3] for a in arr])
      curv=np.array([a[8] for a in arr])
      print(f"  {label}: {len(arr)} events | speed mean={np.mean(v):.1f} | lead present {np.sum(lead)}/{len(arr)} ({np.sum(lead)*100//len(arr)}%)")
      if np.sum(lead):
        lm=lead.astype(bool)
        print(f"     with-lead TTC: mean={np.mean(ttc[lm]):.1f}s p10={np.percentile(ttc[lm],10):.1f} | dRel mean={np.mean(d[lm]):.1f}m")
      print(f"     curvature@override: mean={np.mean(curv):.5f} p95={np.percentile(curv,95):.5f}  (corner? >~0.005)")
      # top 5 by low ttc (closest calls)
      if np.sum(lead):
        order=np.argsort(ttc[lm])[:6]
        print(f"     closest calls (TTC, dRel, vEgo, cmd_accel):")
        for o in order:
          a=[x for x in np.array(arr,dtype=object)[lm][o]]
          print(f"        seg {a[0][-2:]} v={a[1]:.1f} dRel={a[3]:.1f} TTC={a[5]:.1f} cmd_accel={a[6]:.2f} aTarget={a[7]:.2f} curv={a[8]:.5f}")
    summ(brake,"BRAKE overrides")
    summ(gas,"GAS overrides")
