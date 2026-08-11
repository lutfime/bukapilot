#!/usr/bin/env python3
"""GROUND TRUTH: check the ACTUAL steering angle at seg 21 (2:20PM spot) to know if it was
really a corner, vs what the model predicted (what CSC used). If the car steered hard (real
corner) but the model saw straight, CSC missed a real corner."""
import sys, math
sys.path.insert(0, "/Users/WanLutfi/Documents/Xcode/Kommu")
import numpy as np
from cereal import log as capnp_log
import zstandard as zstd
EV = capnp_log.Event
BUDGET=2.0; STEER_RATIO=20.0; WHEELBASE=2.85
def rd(p):
    with open(p,'rb') as f: d=f.read()
    return list(EV.read_multiple_bytes(zstd.ZstdDecompressor().stream_reader(d).read()))

p="/Users/WanLutfi/Documents/Xcode/Kommu/result/drives/2026-08-10--05-58-42--21.rlog.zst"
evs=rd(p); t0=evs[0].logMonoTime/1e9
# collect: t, steeringAngleDeg (actual), vEgo, model max curvature, CSC advisory
rows=[]
for e in evs:
    t=e.logMonoTime/1e9-t0; w=e.which()
    if w=='carState':
        cs=e.carState
        rows.append({'t':t,'steer':float(cs.steeringAngleDeg),'vego':float(cs.vEgo)})
    elif w=='modelV2':
        mv=e.modelV2
        if len(mv.velocity.x):
            vel=np.array(mv.velocity.x); yr=np.array(mv.orientationRate.z)
            if vel.size==yr.size:
                mc=np.max(np.abs(yr/np.maximum(vel,1)))
                if rows: rows[-1]['model_curv']=float(mc)
# find the steering event: max |steeringAngleDeg|
rows=[r for r in rows if 'steer' in r]
if not rows: print("no carState"); sys.exit()
maxsteer=max(rows, key=lambda r:abs(r['steer']))
print(f"seg21: {len(rows)} carState frames")
print(f"steeringAngleDeg range: {min(r['steer'] for r in rows):.0f} to {max(r['steer'] for r in rows):.0f} deg")
print(f"MAX |steer| = {maxsteer['steer']:.0f}deg at t={maxsteer['t']:.0f}s, vEgo={maxsteer['vego']*3.6:.0f}km/h")
# actual corner severity from steering: curvature = steer_rad/(steerRatio*wheelbase)
srad=math.radians(abs(maxsteer['steer']))
act_curv=srad/(STEER_RATIO*WHEELBASE)
act_adv=math.sqrt(BUDGET/act_curv) if act_curv>1e-5 else 999
print(f"\n=== GROUND TRUTH (from actual steering) ===")
print(f"  actual curvature from steering: {act_curv:.5f} (radius {1/act_curv:.0f}m)")
print(f"  REAL safe speed for this corner: {act_adv*3.6:.0f} km/h")
print(f"  your speed: {maxsteer['vego']*3.6:.0f} km/h -> {'NEEDED slowing!' if act_adv<maxsteer['vego'] else 'no slow needed'}")
mc=maxsteer.get('model_curv')
print(f"\n=== what the MODEL/CSC saw at that moment ===")
print(f"  model predicted max curvature: {mc:.5f}" if mc else "  model: no data")
if mc:
    csc_adv=math.sqrt(BUDGET/mc) if mc>1e-5 else 999
    print(f"  CSC advisory (from model): {csc_adv*3.6:.0f} km/h")
    print(f"\n  >>> model curvature {mc:.5f} vs actual {act_curv:.5f}: model {'UNDERDETECTED' if mc<act_curv*0.5 else 'saw it'} the corner")
# show steering timeline around the turn
print(f"\n=== steering timeline (t={maxsteer['t']:.0f}s +/- 4s) ===")
tc=maxsteer['t']
for r in rows:
    if tc-4<=r['t']<=tc+4 and abs(r['t']-tc)<6:
        print(f"  t={r['t']:.0f}s steer={r['steer']:6.0f}deg vEgo={r['vego']*3.6:.0f}km/h model_curv={r.get('model_curv',0):.5f}")
