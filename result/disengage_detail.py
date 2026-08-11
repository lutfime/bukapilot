#!/usr/bin/env python3
"""Detail the spontaneous disengage in 10-30-57 seg12: the alert type + onroadEvents + full carState."""
import sys
sys.path.insert(0, "/Users/WanLutfi/Documents/Xcode/Kommu")
from cereal import log as capnp_log
import zstandard as zstd
EV = capnp_log.Event
import numpy as np

def read_events(path):
    with open(path, 'rb') as f:
        data = f.read()
    return list(EV.read_multiple_bytes(zstd.ZstdDecompressor().stream_reader(data).read()))

p = "/Users/WanLutfi/Documents/Xcode/Kommu/result/drives/2026-08-09--10-30-57--12.rlog.zst"
evs = read_events(p)
print(f"seg12: {len(evs)} events")

prev_en = False
t0 = evs[0].logMonoTime / 1e9 if evs else 0
# find the disengage transition + dump a window around it
trans_t = None
last_cs = None
events_by_t = {}
alerts = []
for e in evs:
    t = e.logMonoTime / 1e9 - t0
    w = e.which()
    if w == 'carState':
        last_cs = e.carState
    elif w == 'selfdriveState':
        en = e.selfdriveState.enabled
        if prev_en and not en and last_cs is not None and last_cs.vEgo > 5:
            # require no driver input -> spontaneous
            if not (last_cs.brakePressed or last_cs.steeringPressed or last_cs.steerFaultTemporary or last_cs.steerFaultPermanent):
                trans_t = t
                print(f"\n=== SPONTANEOUS DISENGAGE at t={t:.1f}s, vEgo={last_cs.vEgo*3.6:.0f}km/h ===")
                cs = last_cs
                print(f"carState: brake={int(cs.brakePressed)} steerPressed={int(cs.steeringPressed)} gas={int(cs.gasPressed)} "
                      f"steerFaultT={int(cs.steerFaultTemporary)} steerFaultP={int(cs.steerFaultPermanent)} "
                      f"cruiseEnabled={int(cs.cruiseState.enabled)} standstill={int(cs.cruiseState.standstill)}")
                if hasattr(cs, 'carFaulted'): print(f"  carFaulted={cs.carFaulted}")
                if hasattr(cs, 'stockAeb'): print(f"  stockAeb={cs.stockAeb}")
                break
        prev_en = en

