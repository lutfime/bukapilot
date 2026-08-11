#!/usr/bin/env python3
"""Scan 2026-08-09 rlogs for active->standby (disengage) events WHILE DRIVING and report the cause.
Cause sources: onroadEvents alerts, carState (brake/steer/fault), cruiseState (stock ACC)."""
import sys, glob
sys.path.insert(0, "/Users/WanLutfi/Documents/Xcode/Kommu")
from cereal import log as capnp_log
import zstandard as zstd
EV = capnp_log.Event

def read_events(path):
    with open(path, 'rb') as f:
        data = f.read()
    r = zstd.ZstdDecompressor().stream_reader(data)
    return list(EV.read_multiple_bytes(r.read()))

DISENGAGE_ALERTS = {"steerTempUnavailable", "steerUnavailable", "SteerTempUnavailable", "SteerUnavailable",
                    "brakePressed", "BrakePressed", "gasPressedOverride", "manualRestart",
                    "controlsFailed", "canError", "relayMalfunction", "speedTooLow",
                    "outOfSpace", "processorOverheat", "deviceIssueImmobilizer"}
OVERRIDE_ALERTS = {"steerOverride", "brakePressed", "gasPressedOverride", "manualRestart"}

for drive_glob in ["2026-08-09--04-01-58", "2026-08-09--05-45-16", "2026-08-09--10-30-57"]:
    files = sorted(glob.glob(f"/Users/WanLutfi/Documents/Xcode/Kommu/result/drives/{drive_glob}--*.rlog.zst"))
    if not files:
        print(f"\n## {drive_glob}: NO local rlogs"); continue
    print(f"\n## {drive_glob}  ({len(files)} rlog segs)")
    prev_enabled = False
    disengages = []
    for f in files:
        try:
            evs = read_events(f)
        except Exception:
            continue
        # index events by time for correlation
        for e in evs:
            w = e.which()
            if w == 'carState':
                cs = e.carState
                # disengage = was engaged (enabled) and now not, while moving
                pass
        # do a single pass: track enabled via selfdriveState, capture cause on transition
    # second cleaner pass per file
    for f in files:
        try:
            evs = read_events(f)
        except Exception:
            continue
        last_cs = None
        for e in evs:
            w = e.which()
            t = e.logMonoTime / 1e9
            if w == 'carState':
                last_cs = e.carState
            elif w == 'onroadEvents':
                pass
            elif w == 'selfdriveState':
                sd = e.selfdriveState
                enabled = sd.enabled
                if prev_enabled and not enabled and last_cs is not None and last_cs.vEgo > 5:
                    # disengage while driving! gather cause
                    cs = last_cs
                    causes = []
                    if cs.brakePressed: causes.append("brakePressed(driver)")
                    if cs.steeringPressed: causes.append("steeringPressed(driver override)")
                    if cs.steerFaultTemporary: causes.append("STEER_FAULT_TEMP(car)")
                    if cs.steerFaultPermanent: causes.append("STEER_FAULT_PERM(car)")
                    cruise = cs.cruiseState
                    if not cruise.enabled: causes.append(f"stockCruiseOFF(enabled={cruise.enabled})")
                    print(f"  DISENGAGE @ vEgo={cs.vEgo*3.6:.0f}km/h: {', '.join(causes) if causes else 'no obvious input'} "
                          f"| steerFaults(t/p)={int(cs.steerFaultTemporary)}/{int(cs.steerFaultPermanent)} cruiseEnabled={cruise.enabled}")
                prev_enabled = enabled
