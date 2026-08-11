#!/usr/bin/env python3
"""Find 60 km/h corners in today's drives (by SPEED, not unreliable timestamps) and trace CSC.
Answers: at a corner where you were going ~60 km/h, what did CSC advise / did it slow?"""
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
    vel=np.array(mv.velocity.x); yr=np.array(mv.orientationRate.z); tt=np.array(mv.orientationRate.t)
    if vel.size==0 or not(vel.size==yr.size==tt.size): return None,None
    cur=yr/np.maximum(vel,1); mc=np.where(vel>=2.0,np.abs(cur),0)
    ttp=np.maximum(tt,1); rd2=(ve-np.sqrt(BUDGET/np.maximum(mc,1e-6)))/ttp
    idx=np.argmax(rd2) if np.any(rd2>0) else np.argmax(mc); c=float(cur[idx])
    return (math.sqrt(BUDGET/c) if c>1e-5 else 999.0), c

# all today's rlogs
files=sorted(glob.glob("/Users/WanLutfi/Documents/Xcode/Kommu/result/drives/2026-08-10--*.rlog.zst"))
print(f"scanning {len(files)} today's rlog segments")
# collect frames at ~60 km/h (50-70) with a real corner (advisory < speed)
slow60=[]   # vEgo 50-70 AND advisory<vEgo
for f in files:
    drive=f.split('--')[1]+'--'+f.split('--')[2].split('-')[0]  # rough drive id
    try: evs=rd(f)
    except: continue
    for e in evs:
        if e.which()!='modelV2': continue
        mv=e.modelV2
        if len(mv.velocity.x)==0: continue
        ve=float(mv.velocity.x[0])
        if not (13.5<ve<19.5): continue   # 50-70 km/h
        a,c=adv(mv,ve)
        if a is None: continue
        if a<ve:  # CSC WOULD slow at this 60km/h corner
            slow60.append((f.split('--')[-1].replace('.rlog.zst',''), ve, a, c))

# overall: at 50-70 km/h, how often would CSC slow?
all60=0; slow60ct=len(slow60)
# recount all60
for f in files:
    try: evs=rd(f)
    except: continue
    for e in evs:
        if e.which()=='modelV2' and len(e.modelV2.velocity.x):
            ve=float(e.modelV2.velocity.x[0])
            if 13.5<ve<19.5: all60+=1
print(f"\nat 50-70 km/h: {all60} frames, CSC would-slow in {slow60ct} ({100*slow60ct/max(all60,1):.1f}%)")
if slow60:
    slow60.sort(key=lambda x:x[2])  # sharpest (lowest advisory) first
    print(f"\n=== 60km/h corners where CSC WOULD slow (sharpest first) ===")
    print(f"{'seg':>5} {'speed':>7} {'advisory':>9} {'curvature':>10} {'radius':>8} {'slow-by':>8}")
    for seg,ve,a,c in slow60[:10]:
        print(f"{seg:>5} {ve*3.6:5.0f}km/h {a*3.6:5.0f}km/h {c:10.5f} {1/c:7.0f}m {(ve-a)*3.6:5.0f}km/h")
else:
    print("\n>>> At 50-70 km/h, CSC NEVER found a corner sharp enough to slow you. Every 60km/h section was gentle enough (advisory>60).")
