#!/usr/bin/env python3
"""Concrete corner trace: at real corners in the afternoon drive, show the actual road
curvature, the CSC advisory = sqrt(2.0/curvature), the car's speed, and whether CSC slowed.
This makes the '90 km/h advisory' claim concrete with real numbers."""
import sys, glob, math
sys.path.insert(0, "/Users/WanLutfi/Documents/Xcode/Kommu")
import numpy as np
from cereal import log as capnp_log
import zstandard as zstd
EV = capnp_log.Event
BUDGET = 2.0
def rd(p):
    with open(p,'rb') as f: d=f.read()
    return list(EV.read_multiple_bytes(zstd.ZstdDecompressor().stream_reader(d).read()))
def curv_at_worst(mv, vego):
    vel=np.array(mv.velocity.x); yr=np.array(mv.orientationRate.z); tt=np.array(mv.orientationRate.t)
    if vel.size==0 or not (vel.size==yr.size==tt.size): return None
    cur=yr/np.maximum(vel,1); mc=np.where(vel>=2.0, np.abs(cur),0)
    ttp=np.maximum(tt,1); rd_dec=(vego-np.sqrt(BUDGET/np.maximum(mc,1e-6)))/ttp
    idx=np.argmax(rd_dec) if np.any(rd_dec>0) else np.argmax(mc)
    return float(cur[idx]), float(mc.max())

files=sorted(glob.glob("/Users/WanLutfi/Documents/Xcode/Kommu/result/drives/2026-08-10--05-58-42--*.rlog.zst"))
rows=[]  # (t, vego, curvature_worst, peak_curv, advisory, lat, lon)
for f in files:
    seg=f.split('--')[-1].replace('.rlog.zst','')
    try: evs=rd(f)
    except: continue
    t0=evs[0].logMonoTime/1e9 if evs else 0
    last_gps=None
    for e in evs:
        if e.which()=='gpsLocation' and e.gpsLocation.hasFix:
            last_gps=(e.gpsLocation.latitude, e.gpsLocation.longitude)
        elif e.which()=='modelV2':
            mv=e.modelV2
            if len(mv.velocity.x)==0: continue
            ve=float(mv.velocity.x[0])
            if ve<3: continue
            r=curv_at_worst(mv,ve)
            if r is None: continue
            cw,peak=r
            adv=math.sqrt(BUDGET/cw) if cw>1e-5 else 200
            t=e.logMonoTime/1e9-t0
            rows.append((t, ve, cw, peak, adv, last_gps))

if not rows:
    print("no data"); sys.exit()
rows.sort()
advisories=np.array([r[4] for r in rows]); vEgos=np.array([r[1] for r in rows])
print(f"afternoon drive: {len(rows)} frames, speed median {np.median(vEgos)*3.6:.0f}km/h")
print(f"CSC advisory: median {np.median(advisories)*3.6:.0f}km/h, p10 {np.percentile(advisories,10)*3.6:.0f}km/h, min {advisories.min()*3.6:.0f}km/h\n")

print("=== SHARPEST corners (lowest advisory) — where CSC WOULD slow ===")
sharp=sorted(rows, key=lambda r:r[4])[:6]
for t,ve,cw,peak,adv,gps in sharp:
    print(f"  t={t:.0f}s adv={adv*3.6:.0f}km/h (curv={cw:.5f}, radius={1/cw:.0f}m) | your speed={ve*3.6:.0f}km/h | {'WOULD SLOW' if adv<ve else 'no'}")

print("\n=== A GENTLE corner (typical) — why no slowdown ===")
# a typical median-advisory frame at speed
gentle=[r for r in rows if r[1]>8]  # moving
gentle.sort(key=lambda r:abs(r[4]-np.median(advisories)))
for t,ve,cw,peak,adv,gps in gentle[:4]:
    print(f"  t={t:.0f}s adv={adv*3.6:.0f}km/h (curv={cw:.5f}, radius={1/cw:.0f}m) | your speed={ve*3.6:.0f}km/h | {'WOULD SLOW' if adv<ve else 'no (speed<advisory)'}")
