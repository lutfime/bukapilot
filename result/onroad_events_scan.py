#!/usr/bin/env python3
"""Dump disengage-causing onroadEvents (type override/noEntry) in seg11+seg12 of 10-30-57.
These carry the REAL disengage reason (steer faults often appear here, not in carState flags)."""
import sys, glob
sys.path.insert(0, "/Users/WanLutfi/Documents/Xcode/Kommu")
from cereal import log as capnp_log
import zstandard as zstd
EV = capnp_log.Event
from collections import Counter

def read_events(path):
    with open(path, 'rb') as f: data = f.read()
    return list(EV.read_multiple_bytes(zstd.ZstdDecompressor().stream_reader(data).read()))

for seg in ["11", "12"]:
    p = f"/Users/WanLutfi/Documents/Xcode/Kommu/result/drives/2026-08-09--10-30-57--{seg}.rlog.zst"
    evs = read_events(p)
    t0 = evs[0].logMonoTime/1e9
    print(f"\n===== seg{seg} =====")
    # collect all event names that ever carry override/noEntry type (disengage-causing)
    disengage_names = Counter()
    examples = {}
    for e in evs:
        if e.which() != 'onroadEvents':
            continue
        t = e.logMonoTime/1e9 - t0
        for ev in e.onroadEvents:
            types = list(ev.type)
            if any(x in types for x in ['override', 'noEntry', 'preNoEntry']):
                nm = str(ev.name)
                disengage_names[nm] += 1
                if nm not in examples:
                    examples[nm] = t
    print("disengage-causing events (name: count, first@t):")
    for nm, c in disengage_names.most_common():
        print(f"  {nm}: {c}x  (first @ t={examples[nm]:.1f}s)")
