#!/usr/bin/env python3
"""Find the ~2:25pm corner in segs 25-28 by STEERING (ground truth), then trace CSC advisory,
vEgo decel, and lead presence through it. Is the slowdown CSC (no lead) or masked by a lead?"""
import sys, glob, math
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

files=sorted(glob.glob("/Users/WanLutfi/Documents/Xcode/Kommu/result/drives/2026-08-10--05-58-42--2[5-8].rlog.zst"))
# build timeline across segs
allrows=[]; last_plan=None; last_lead=False; last_steer=0; last_ve=0
for f in files:
    seg=f.split('--')[-1].replace('.rlog.zst','')
    t0_off=int(seg)*60  # rough segment offset (sec)
    try: evs=rd(f)
    except: continue
    t0=evs[0].logMonoTime/1e9
    for e in evs:
        t=e.logMonoTime/1e9-t0+t0_off; w=e.which()
        if w=='carState': last_steer=float(e.carState.steeringAngleDeg); last_ve=float(e.carState.vEgo)
        elif w=='longitudinalPlan': last_plan=e.longitudinalPlan
        elif w=='radarState': last_lead=bool(e.radarState.leadOne.status)
        elif w=='modelV2' and len(e.modelV2.velocity.x):
            allrows.append((t,last_steer,last_ve,adv(e.modelV2,last_ve),float(last_plan.aTarget) if last_plan else 0,last_lead,int(seg)))

# find corners: max |steer| with vEgo>30 (a real driving corner)
corners=[r for r in allrows if abs(r[1])>30 and r[2]>11.0]  # steer>40deg, >29km/h
if not corners:
    print("no sharp corner (steer>40) in segs 25-28; showing max steer found")
    corners=[max([x for x in allrows if x[2]>11.0],key=lambda r:abs(r[1]))]
corner=max(corners,key=lambda r:abs(r[1]))
seg=corner[6]; tc=corner[0]
print(f"=== corner found: seg{seg} steer={corner[1]:.0f}deg vEgo={corner[2]*3.6:.0f}km/h CSCadv={corner[3]*3.6 if corner[3] else 0:.0f}km/h ===")
print(f"\n{'t':>6} {'seg':>4} {'steer':>6} {'vEgo':>6} {'CSCadv':>7} {'aTarget':>8} {'lead':>5}")
for t,steer,ve,a,atgt,lead,s in allrows:
    if tc-4<=t<=tc+4:
        print(f"{t:6.0f} {s:>4} {steer:6.0f} {ve*3.6:4.0f}km {a*3.6 if a else 0:5.0f}km {atgt:+.2f}   {lead}")
# vEgo drop + lead during corner
vw=[r[2] for r in allrows if tc-3<=r[0]<=tc+2]
lw=[r[5] for r in allrows if tc-3<=r[0]<=tc+2]
print(f"\nvEgo: {max(vw)*3.6:.0f}->{min(vw)*3.6:.0f} km/h (drop {(max(vw)-min(vw))*3.6:.0f}km/h) | lead present {sum(lw)}/{len(lw)} frames")
