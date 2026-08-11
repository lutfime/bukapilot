#!/usr/bin/env python3
"""CSC activity check on today's drive: did CSC ever advise a speed BELOW vEgo (would clamp)?
If clamp-fraction ~0, CSC never needed to slow -> explains 'no slowdown felt'."""
import sys, glob, math
sys.path.insert(0, "/Users/WanLutfi/Documents/Xcode/Kommu")
import numpy as np
from cereal import log as capnp_log
import zstandard as zstd
EV = capnp_log.Event
BUDGET = 2.0  # Standard
MIN_SPD = 1.39  # MINIMUM_PLANNED_SPEED approx
def rd(p):
    with open(p,'rb') as f: d=f.read()
    return list(EV.read_multiple_bytes(zstd.ZstdDecompressor().stream_reader(d).read()))
def advisory(mv, vego):
    vel=np.array(mv.velocity.x); yr=np.array(mv.orientationRate.z); tt=np.array(mv.orientationRate.t)
    if vel.size==0 or yr.size==0 or tt.size==0 or not (vel.size==yr.size==tt.size): return None
    cur=yr/np.maximum(vel,1); mc=np.where(vel>=MIN_SPD, np.abs(cur),0)
    ttp=np.maximum(tt,1); rd_dec=(vego-np.sqrt(BUDGET/np.maximum(mc,1e-6)))/ttp
    idx=np.argmax(rd_dec) if (BUDGET>0 and np.any(rd_dec>0)) else np.argmax(mc)
    c=float(cur[idx])
    return math.sqrt(BUDGET/c) if c>1e-5 else None
for drive in ["2026-08-10--00-39-04", "2026-08-10--05-58-42"]:
    files=sorted(glob.glob(f"/Users/WanLutfi/Documents/Xcode/Kommu/result/drives/{drive}--*.rlog.zst"))
    if not files: continue
    n=0; clamp=0; advisories=[]; vEgos=[]; min_adv=999
    for f in files:
        for e in rd(f):
            if e.which()!='modelV2': continue
            mv=e.modelV2
            if len(mv.velocity.x)==0: continue
            ve=float(mv.velocity.x[0])
            if ve<3: continue  # skip stopped
            a=advisory(mv,ve)
            if a is None: continue
            n+=1; advisories.append(a); vEgos.append(ve)
            if a<ve: clamp+=1
            min_adv=min(min_adv,a)
    if n:
        advisories=np.array(advisories); vEgos=np.array(vEgos)
        would_slow = (advisories < vEgos).sum()
        print(f"{drive}: {n} frames | CSC advisory<vego (WOULD SLOW): {would_slow} ({100*would_slow/n:.1f}%) | min advisory {min_adv*3.6:.0f}km/h")
        # when it would slow, by how much?
        slow = advisories[vEgos>15]  # at speed >54km/h
        ve_fast = vEgos[vEgos>15]
        ws = (slow<ve_fast).sum() if len(slow) else 0
        print(f"   at >54km/h: would-slow {ws}/{len(slow)} frames; advisory median {np.median(advisories)*3.6:.0f}km/h (vs vEgo median {np.median(vEgos)*3.6:.0f}km/h)")
