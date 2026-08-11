#!/usr/bin/env python3
"""Trace seg 21 of 05-58-42 (the ~2:20PM 60km/h corner): did the car ACTUALLY decelerate
toward the CSC advisory, or stay at speed? Check vEgo, aTarget, lead, brake over time."""
import sys, math
sys.path.insert(0, "/Users/WanLutfi/Documents/Xcode/Kommu")
import numpy as np
from cereal import log as capnp_log
import zstandard as zstd
EV = capnp_log.Event
BUDGET=2.0
def rd(p):
    with open(p,'rb') as f: d=f.read()
    return list(EV.read_multiple_bytes(zstd.ZstdDecompressor().stream_reader(d).read()))
def adv(mv,ve):
    vel=np.array(mv.velocity.x); yr=np.array(mv.orientationRate.z); tt=np.array(mv.orientationRate.t)
    if vel.size==0 or not(vel.size==yr.size==tt.size): return None
    cur=yr/np.maximum(vel,1); mc=np.where(vel>=2.0,np.abs(cur),0)
    ttp=np.maximum(tt,1); rd2=(ve-np.sqrt(BUDGET/np.maximum(mc,1e-6)))/ttp
    idx=np.argmax(rd2) if np.any(rd2>0) else np.argmax(mc); c=float(cur[idx])
    return math.sqrt(BUDGET/c) if c>1e-5 else 999.0

p="/Users/WanLutfi/Documents/Xcode/Kommu/result/drives/2026-08-10--05-58-42--21.rlog.zst"
evs=rd(p); t0=evs[0].logMonoTime/1e9
# build per-tick: vEgo, advisory, aTarget (longitudinalPlan), lead, brake
rows=[]
last_plan=None; last_lead=None; last_cs=None
for e in evs:
    t=e.logMonoTime/1e9-t0
    w=e.which()
    if w=='carState': last_cs=e.carState
    elif w=='longitudinalPlan': last_plan=e.longitudinalPlan
    elif w=='radarState': last_lead=e.radarState.leadOne
    elif w=='modelV2':
        mv=e.modelV2
        if len(mv.velocity.x)==0: continue
        ve=float(mv.velocity.x[0]); a=adv(mv,ve)
        atgt=float(last_plan.aTarget) if last_plan else None
        lead=bool(last_lead.status) if last_lead else False
        brake=int(last_cs.brakePressed) if last_cs else 0
        rows.append((t,ve,a,atgt,lead,brake))
# focus on the corner: where advisory < ve (CSC wants to slow)
corner=[r for r in rows if r[2] is not None and r[2]<r[1] and r[1]>13]
if not corner:
    print("no 60km/h would-slow corner in seg21?");
    # show vEgo range
    ves=[r[1] for r in rows]; print(f"vEgo range: {min(ves)*3.6:.0f}-{max(ves)*3.6:.0f} km/h")
else:
    tstart=corner[0][0]
    print(f"corner window starts t={tstart:.0f}s. vEgo/advisory/aTarget/lead/brake:")
    print(f"{'t(s)':>6} {'vEgo':>7} {'CSCadv':>7} {'aTarget':>8} {'lead':>5} {'brake':>6}")
    for t,ve,a,atgt,lead,brake in rows:
        if tstart-3<=t<=tstart+12:
            ats=f"{atgt:+.2f}" if atgt is not None else "?"
            print(f"{t:6.0f} {ve*3.6:5.0f}km {a*3.6 if a else 0:5.0f}km {ats:>8} {str(lead):>5} {brake:6d}")
