#!/usr/bin/env python3
"""Find the REAL 2:20 PM moment via absolute time (logMonoTime -> MYT UTC+8), not a guessed
offset. Show speed + curvature + CSC advisory there. Also: does CSC check all the time or
only at corners? (It computes every cycle; on straights advisory ~ infinite = no clamp.)"""
import sys, glob, math, datetime
sys.path.insert(0, "/Users/WanLutfi/Documents/Xcode/Kommu")
import numpy as np
from cereal import log as capnp_log
import zstandard as zstd
EV = capnp_log.Event
BUDGET = 2.0
def rd(p):
    with open(p,'rb') as f: d=f.read()
    return list(EV.read_multiple_bytes(zstd.ZstdDecompressor().stream_reader(d).read()))
def advisory(mv, vego):
    vel=np.array(mv.velocity.x); yr=np.array(mv.orientationRate.z); tt=np.array(mv.orientationRate.t)
    if vel.size==0 or not (vel.size==yr.size==tt.size): return None, None
    cur=yr/np.maximum(vel,1); mc=np.where(vel>=2.0, np.abs(cur),0)
    ttp=np.maximum(tt,1); rd_dec=(vego-np.sqrt(BUDGET/np.maximum(mc,1e-6)))/ttp
    idx=np.argmax(rd_dec) if np.any(rd_dec>0) else np.argmax(mc)
    c=float(cur[idx])
    return (math.sqrt(BUDGET/c) if c>1e-5 else 999.0), c

files=sorted(glob.glob("/Users/WanLutfi/Documents/Xcode/Kommu/result/drives/2026-08-10--05-58-42--*.rlog.zst"))
print(f"scanning {len(files)} afternoon segments (segs {files[0].split('--')[-1].replace('.rlog.zst','')}-{files[-1].split('--')[-1].replace('.rlog.zst','')})")
# show the absolute MYT time range covered, then find ~14:20 (2:20 PM)
all_frames=[]
for f in files:
    try: evs=rd(f)
    except: continue
    for e in evs:
        if e.which()!='modelV2': continue
        mv=e.modelV2
        if len(mv.velocity.x)==0: continue
        utc=datetime.datetime.utcfromtimestamp(e.logMonoTime/1e9)
        myt=utc+datetime.timedelta(hours=8)
        ve=float(mv.velocity.x[0])
        adv,c=advisory(mv,ve)
        all_frames.append((myt, ve, adv, c))

if not all_frames:
    print("no frames"); sys.exit()
all_frames.sort()
print(f"time range (MYT): {all_frames[0][0].strftime('%H:%M:%S')} to {all_frames[-1][0].strftime('%H:%M:%S')}")

# find frames near 14:20 MYT (2:20 PM)
target_hour=14; target_min=20
near=[fr for fr in all_frames if fr[0].hour==target_hour and abs(fr[0].minute-target_min)<=2]
if near:
    print(f"\n=== ~2:20 PM ({target_hour}:{target_min:02d} MYT) — {len(near)} frames ===")
    print(f"{'MYT time':>10} {'speed':>7} {'CSC advisory':>12} {'curvature':>10} {'radius':>8} {'slowed?':>8}")
    # sample a few
    for myt,ve,adv,c in near[::max(1,len(near)//8)]:
        r=1/c if c>1e-5 else 99999
        print(f"{myt.strftime('%H:%M:%S'):>10} {ve*3.6:5.0f}km/h {adv*3.6:7.0f}km/h {c:10.5f} {r:7.0f}m {'YES' if adv<ve else 'no':>8}")
else:
    print(f"\nNo frames at 14:20 MYT in the segments I have. Closest times: {all_frames[0][0].strftime('%H:%M')} ... {all_frames[-1][0].strftime('%H:%M')}")
    # show whatever is closest to 14:20
    closest=min(all_frames, key=lambda fr: abs((fr[0].hour*60+fr[0].minute)-(14*60+20)))
    print(f"closest frame: {closest[0].strftime('%H:%M:%S')} speed={closest[1]*3.6:.0f}km/h adv={closest[2]*3.6:.0f}km/h")
