#!/usr/bin/env python3
"""Compare the TWO curvature signals at the seg21 corner:
- action.desiredCurvature (what the STEERING uses — works, car turned correctly)
- orientationRate.z/velocity.x (what CSC uses — read 0)
If desiredCurvature is non-zero but orientationRate is 0, CSC is reading the wrong signal."""
import sys, math
sys.path.insert(0, "/Users/WanLutfi/Documents/Xcode/Kommu")
import numpy as np
from cereal import log as capnp_log
import zstandard as zstd
EV = capnp_log.Event
def rd(p):
    with open(p,'rb') as f: d=f.read()
    return list(EV.read_multiple_bytes(zstd.ZstdDecompressor().stream_reader(d).read()))

p="/Users/WanLutfi/Documents/Xcode/Kommu/result/drives/2026-08-10--05-58-42--21.rlog.zst"
evs=rd(p); t0=evs[0].logMonoTime/1e9
# at each modelV2: desiredCurvature (steering signal) vs max(|orientationRate.z/velocity|) (CSC signal) + steering angle
rows=[]
last_steer=0
for e in evs:
    t=e.logMonoTime/1e9-t0; w=e.which()
    if w=='carState': last_steer=float(e.carState.steeringAngleDeg)
    elif w=='modelV2':
        mv=e.modelV2
        dc=float(mv.action.desiredCurvature) if mv.action is not None else 0.0
        vel=np.array(mv.velocity.x); yr=np.array(mv.orientationRate.z)
        csc_curv=float(np.max(np.abs(yr/np.maximum(vel,1)))) if (vel.size and yr.size and vel.size==yr.size) else 0.0
        rows.append((t,last_steer,dc,csc_curv))
# find the corner: max |steer|
corner=max(rows,key=lambda r:abs(r[1]))
print(f"corner at t={corner[0]:.0f}s: steer={corner[1]:.0f}deg")
print(f"\n{'t(s)':>6} {'steer':>6} {'desiredCurv (STEERING)':>24} {'orientationRate/vel (CSC)':>26}")
tc=corner[0]
for t,steer,dc,cc in rows:
    if tc-2<=t<=tc+3:
        print(f"{t:6.0f} {steer:6.0f} {dc:24.5f} {cc:26.5f}")
print(f"\n=== verdict ===")
dcs=[r[2] for r in rows if tc-2<=r[0]<=tc+3]
ccs=[r[3] for r in rows if tc-2<=r[0]<=tc+3]
print(f"desiredCurvature at corner: median {np.median(dcs):.5f} (non-zero = steering KNEW the corner)")
print(f"orientationRate/vel at corner: median {np.median(ccs):.5f} (what CSC used)")
if np.median(dcs)>0.001 and np.median(ccs)<0.001:
    print(">>> CSC is reading the WRONG signal: desiredCurvature sees the corner, orientationRate doesn't.")
    print(">>> FIX: CSC should use action.desiredCurvature (like the steering controller), not orientationRate.z/velocity.")
