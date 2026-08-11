import sys, math
sys.path.insert(0, "/Users/WanLutfi/Documents/Xcode/Kommu")
import numpy as np
from cereal import log as capnp_log
import zstandard as zstd
EV = capnp_log.Event; STEER_MAX=580
def rd(p):
    with open(p,'rb') as f: d=f.read()
    return list(EV.read_multiple_bytes(zstd.ZstdDecompressor().stream_reader(d).read()))
p="/Users/WanLutfi/Documents/Xcode/Kommu/result/drives/2026-08-10--05-58-42--25.rlog.zst"
evs=rd(p); t0=evs[0].logMonoTime/1e9
rows=[]; last_ve=0
for e in evs:
    t=e.logMonoTime/1e9-t0; w=e.which()
    if w=='carState': last_ve=float(e.carState.vEgo)
    elif w=='controlsState':
        cs=e.controlsState
        if cs.lateralControlState.which()=='pidState':
            ps=cs.lateralControlState.pidState
            rows.append((t,float(ps.steeringAngleDeg),float(ps.steeringAngleDesiredDeg),float(ps.angleError),float(ps.output)*STEER_MAX,last_ve))
corner=max([r for r in rows if r[5]>8.0],key=lambda r:abs(r[1]))
tc=corner[0]
print(f"corner: actual steer={corner[1]:.0f}deg desired={corner[2]:.0f}deg err={corner[3]:.1f}deg @ vEgo={corner[5]*3.6:.0f}km/h")
print(f"\n{'t':>5} {'actual':>7} {'desired':>7} {'err':>6} {'CANcmd':>7} {'vEgo':>5}")
for t,act,des,err,out,ve in rows:
    if tc-3<=t<=tc+4: print(f"{t:5.0f} {act:7.0f} {des:7.0f} {err:6.1f} {out:7.0f} {ve*3.6:4.0f}k")
W=[r for r in rows if tc-3<=r[0]<=tc+4]
cmds=np.array([r[4] for r in W]); act=np.array([r[1] for r in W]); err=np.array([r[3] for r in W])
dcmd=np.abs(np.diff(cmds))
print(f"\nCAN cmd: range {cmds.min():.0f}-{cmds.max():.0f}, step p95 {np.percentile(dcmd,95):.0f}, max {dcmd.max():.0f}")
print(f"reversals (sign flips, |cmd|>5): {np.sum(np.diff(np.sign(cmds[np.abs(cmds)>5]))!=0)}")
print(f"angleError: abs mean {np.abs(err).mean():.1f}deg, max {np.abs(err).max():.1f}deg (oversteer if err large/oscillating)")
