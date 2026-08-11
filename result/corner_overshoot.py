#!/usr/bin/env python3
"""Corner overshoot analysis on the full afternoon drive. For each corner (steer peak):
- entry/apex/exit angleError (oversteer = actual>desired = neg error; undershoot = pos)
- integrator (i) during + after (windup -> slow release -> overshoot)
- does the car OVERSHOOT after the corner (exit error opposite sign + large)?
This explains the 'drifts to other lane after corner' feel."""
import sys, glob, math
sys.path.insert(0, "/Users/WanLutfi/Documents/Xcode/Kommu")
import numpy as np
from cereal import log as capnp_log
import zstandard as zstd
EV = capnp_log.Event
def rd(p):
    with open(p,'rb') as f: d=f.read()
    return list(EV.read_multiple_bytes(zstd.ZstdDecompressor().stream_reader(d).read()))

files=sorted(glob.glob("/Users/WanLutfi/Documents/Xcode/Kommu/result/drives/2026-08-10--05-58-42--*.rlog.zst"))
# build timeline: t, steeringAngleDeg, angleError, vEgo, pid i, pid output
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
                rows.append((t,float(ps.steeringAngleDeg),float(ps.angleError),last_ve,float(ps.i),float(ps.output)))

# find corners: local maxima of |steer| (peaks), spaced >5s apart, |steer|>25, vEgo>4
corners=[]
last_t=-99
for i in range(2,len(rows)-2):
    s=abs(rows[i][1])
    if s>25 and s>=abs(rows[i-1][1]) and s>=abs(rows[i+1][1]) and rows[i][3]>4 and rows[i][0]-last_t>5:
        corners.append(i); last_t=rows[i][0]
print(f"found {len(corners)} corners (steer peak >25deg, vEgo>4m/s)")

# for each corner: entry (t-2), apex (t), exit (t+2.5), late-exit (t+4)
def at(ti, dt):
    tt=rows[ti][0]+dt
    # find nearest row
    best=min(range(max(0,ti-300),min(len(rows),ti+300)), key=lambda k:abs(rows[k][0]-tt))
    return rows[best]

overshoot_ct=0; understeer_entry=0
ent_err=[]; exit_err=[]; apex_i=[]; exit_i=[]
for c in corners:
    ent=at(c,-2.0); apex=rows[c]; ex=at(c,2.5); late=at(c,4.0)
    ent_err.append(ent[2]); exit_err.append(ex[2]); apex_i.append(apex[4]); exit_i.append(ex[4])
    # overshoot: exit error OPPOSITE sign to apex steer direction + |err|>3
    steer_dir=1 if apex[1]>0 else -1
    if ex[2]*steer_dir < -3:   # error opposite to steer -> overcorrected/overshoot
        overshoot_ct+=1
    if ent[2]*steer_dir > 2:   # entry error same sign as steer -> undershooting (lagging)
        understeer_entry+=1

ent_err=np.array(ent_err); exit_err=np.array(exit_err); apex_i=np.array(apex_i); exit_i=np.array(exit_i)
print(f"\n=== corner dynamics (mean across {len(corners)} corners) ===")
print(f"  ENTRY (t-2s): angleError mean {np.mean(np.abs(ent_err)):.1f}deg (undershoot if same sign as steer)")
print(f"  APEX:         integrator(i) mean {np.mean(apex_i):.3f}")
print(f"  EXIT (t+2.5s):angleError mean {np.mean(np.abs(exit_err)):.1f}deg  | integrator(i) {np.mean(exit_i):.3f} (still wound up?)")
print(f"\n  >>> entry UNDERSTEER (lagged turn-in): {understeer_entry}/{len(corners)} corners")
print(f"  >>> exit OVERSHOOT (overcorrected to other side): {overshoot_ct}/{len(corners)} corners")
print(f"  integrator: apex {np.mean(apex_i):.3f} -> exit {np.mean(exit_i):.3f} ({'still wound up (slow release -> overshoot)' if np.mean(exit_i)>np.mean(apex_i)*0.5 else 'released'})")
