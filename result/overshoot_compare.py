#!/usr/bin/env python3
"""Compare overshoot MAGNITUDE (not just count) between old ki + new ki drives.
If the decreased ki made each overshoot MILDER (smaller), the user would feel
'doesn't overshoot anymore' even with a similar count."""
import sys, glob, math
sys.path.insert(0, "/Users/WanLutfi/Documents/Xcode/Kommu")
import numpy as np
from cereal import log as capnp_log
import zstandard as zstd
EV = capnp_log.Event
def rd(p):
    with open(p,'rb') as f: d=f.read()
    return list(EV.read_multiple_bytes(zstd.ZstdDecompressor().stream_reader(d).read()))

def analyze(drive_glob, label):
    files=sorted(glob.glob(drive_glob))
    rows=[]; last_ve=0
    for f in files:
        try: evs=rd(f)
        except: continue
        t0=evs[0].logMonoTime/1e9
        for e in evs:
            t=e.logMonoTime/1e9-t0; w=e.which()
            if w=='carState': last_ve=float(e.carState.vEgo)
            elif w=='controlsState':
                cs=e.controlsState
                if cs.lateralControlState.which()=='pidState':
                    ps=cs.lateralControlState.pidState
                    rows.append((t,float(ps.steeringAngleDeg),float(ps.angleError),last_ve,float(ps.i)))
    # find corners
    corners=[]; last_t=-99
    for i in range(2,len(rows)-2):
        s=abs(rows[i][1])
        if s>25 and s>=abs(rows[i-1][1]) and s>=abs(rows[i+1][1]) and rows[i][3]>4 and rows[i][0]-last_t>5:
            corners.append(i); last_t=rows[i][0]
    def at(ti, dt):
        tt=rows[ti][0]+dt
        best=min(range(max(0,ti-300),min(len(rows),ti+300)), key=lambda k:abs(rows[k][0]-tt))
        return rows[best]
    # overshoot corners + their magnitude
    overshoot_mags=[]
    entry_errs=[]
    for c in corners:
        ex=at(c,2.5); ent=at(c,-2.0)
        steer_dir=1 if rows[c][1]>0 else -1
        if ex[2]*steer_dir < -2:  # overshoot (opposite error >2deg)
            overshoot_mags.append(abs(ex[2]))
        entry_errs.append(abs(ent[2]))
    om=np.array(overshoot_mags) if overshoot_mags else np.array([0])
    ee=np.array(entry_errs)
    print(f"\n{label}: {len(corners)} corners, {len(overshoot_mags)} overshoot")
    print(f"  overshoot MAGNITUDE: median {np.median(om):.1f}deg, max {om.max():.1f}deg (smaller = milder = less felt)")
    print(f"  entry error: median {np.median(ee):.1f}deg (not mean — robust to outliers)")

analyze("result/drives/2026-08-10--05-58-42--*.rlog.zst", "OLD ki [0.001,0.01,0.09,0.4,0.5]")
analyze("result/drives/2026-08-10--11-04-40--*.rlog.zst", "NEW ki [0.001,0.005,0.05,0.4,0.5]")
