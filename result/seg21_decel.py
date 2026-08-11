#!/usr/bin/env python3
"""Definitive: through the seg21 corner (54deg steer ~61km/h), did the car ACTUALLY decelerate,
and was it CSC (v_cruise clamp) or a lead? Trace vEgo, steer, lead, aTarget, CSC advisory."""
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
    vel=np.array(mv.velocity.x); yr=np.array(mv.orientationRate.z)
    if vel.size==0 or vel.size!=yr.size: return None
    mc=np.max(np.abs(yr/np.maximum(vel,1)))
    return math.sqrt(BUDGET/mc) if mc>1e-5 else 999.0
p="/Users/WanLutfi/Documents/Xcode/Kommu/result/drives/2026-08-10--05-58-42--21.rlog.zst"
evs=rd(p); t0=evs[0].logMonoTime/1e9
rows=[]; last_plan=None; last_lead=False; last_steer=0
for e in evs:
    t=e.logMonoTime/1e9-t0; w=e.which()
    if w=='carState': last_steer=float(e.carState.steeringAngleDeg); last_ve=float(e.carState.vEgo)
    elif w=='longitudinalPlan': last_plan=e.longitudinalPlan
    elif w=='radarState': last_lead=bool(e.radarState.leadOne.status)
    elif w=='modelV2':
        mv=e.modelV2
        if len(mv.velocity.x):
            rows.append((t,last_steer,last_ve,adv(mv,last_ve),float(last_plan.aTarget) if last_plan else 0,last_lead))
# window around the corner: t 1285-1302
print(f"{'t':>5} {'steer':>6} {'vEgo':>6} {'CSCadv':>7} {'aTarget':>8} {'lead':>5}")
for t,steer,ve,a,atgt,lead in rows:
    if 1285<t<1302:
        # sample ~every 0.5s
        print(f"{t:5.0f} {steer:6.0f} {ve*3.6:4.0f}km {a*3.6 if a else 0:5.0f}km {atgt:+.2f}   {lead}")
# vEgo min/max in window
vw=[r[2] for r in rows if 1288<r[0]<1298]
print(f"\nvEgo in corner window (t1288-1298): {min(vw)*3.6:.0f}-{max(vw)*3.6:.0f} km/h (did it drop?)")
