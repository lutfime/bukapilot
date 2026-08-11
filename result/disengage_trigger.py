#!/usr/bin/env python3
"""Contiguous seg11+seg12: at each active->standby disengage, dump the onroadEvents active
in the 0.6s BEFORE the transition = the actual trigger. Resolves steerOverride vs truly-nothing."""
import sys
sys.path.insert(0, "/Users/WanLutfi/Documents/Xcode/Kommu")
from cereal import log as capnp_log
import zstandard as zstd
EV = capnp_log.Event
def rd(p):
    with open(p,'rb') as f: d=f.read()
    return list(EV.read_multiple_bytes(zstd.ZstdDecompressor().stream_reader(d).read()))
evs = rd("/Users/WanLutfi/Documents/Xcode/Kommu/result/drives/2026-08-09--10-30-57--11.rlog.zst") + \
      rd("/Users/WanLutfi/Documents/Xcode/Kommu/result/drives/2026-08-09--10-30-57--12.rlog.zst")
evs.sort(key=lambda e: e.logMonoTime)
t0 = evs[0].logMonoTime/1e9
# build timeline
prev_en=False; last_cs=None
recent_events=[]  # (t, set(names)) from onroadEvents
recent_cs=[]      # (t, steeringPressed, brakePressed, vEgo)
for e in evs:
    t = e.logMonoTime/1e9 - t0
    w=e.which()
    if w=='carState':
        last_cs=e.carState
        recent_cs.append((t,int(e.carState.steeringPressed),int(e.carState.brakePressed),e.carState.vEgo))
        recent_cs=[x for x in recent_cs if t-x[0]<1.0]
    elif w=='onroadEvents':
        names=set(str(ev.name) for ev in e.onroadEvents)
        recent_events.append((t,names))
        recent_events=[x for x in recent_events if t-x[0]<0.7]
    elif w=='selfdriveState':
        en=e.selfdriveState.enabled
        if prev_en and not en and last_cs and last_cs.vEgo>5:
            # union of event names active in last 0.6s
            active=set()
            for tt,names in recent_events:
                active|=names
            # carState steering/brake in last 0.5s
            steer_recent = any(s for _,s,_,_ in recent_cs)
            brake_recent = any(b for _,_,b,_ in recent_cs)
            print(f"DISENGAGE @ t={t:.1f}s vEgo={last_cs.vEgo*3.6:.0f}km/h")
            print(f"  events active prior 0.6s: {sorted(active) if active else 'NONE'}")
            print(f"  steeringPressed in prior 0.5s: {steer_recent} | brakePressed: {brake_recent}")
        prev_en=en
