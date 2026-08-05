#!/usr/bin/env python3
"""
analyze_corners.py — offline corner analysis from local rlog files.

Reads downloaded .rlog (raw capnp-framed) or .qlog.zst files directly via capnp
(no LogReader / zmq needed) and reports, for every corner above a lateral-accel
threshold: speed, model prediction, commanded accel, steering saturation, torqued.

USAGE:
    /tmp/oplog-venv/bin/python tools-local/analyze_corners.py \\
        result/drives/2026-08-05--09-43-32--*.rlog

    --threshold 1.0   min a_y (m/s^2) to count as a corner
    --csv out.csv     also write per-sample CSV
"""
import argparse, glob, math, os, sys
import numpy as np
import capnp
import zstandard as zstd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from cereal import log as capnp_log

X70_WHEELBASE = 2.67
X70_STEER_RATIO = 15.0
MIN_SPEED_MPS = 8.0


def a_y_from_steering(v_ego, ang):
    if v_ego < 0.5:
        return 0.0
    return (v_ego ** 2) * abs(ang) * math.pi / 180.0 / (X70_STEER_RATIO * X70_WHEELBASE)


def read_events(path):
    """Yield capnp Event objects from a raw .rlog or .qlog.zst file."""
    with open(path, "rb") as f:
        dat = f.read()
    if path.endswith(".zst"):
        dat = zstd.ZstdDecompressor().decompress(dat)
    # raw rlog is a concatenation of length-framed capnp Event messages;
    # read_multiple_bytes walks them.
    for evt in capnp_log.Event.read_multiple_bytes(dat):
        yield evt


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("logs", nargs="+")
    ap.add_argument("--threshold", type=float, default=1.0)
    ap.add_argument("--csv", default=None)
    args = ap.parse_args()

    files = []
    for pat in args.logs:
        if os.path.exists(pat): files.append(pat)
        else: files.extend(sorted(glob.glob(pat)))
    files = sorted(set(files))
    if not files:
        print("no files matched"); return

    print(f"reading {len(files)} file(s):")
    for f in files: print(f"  {os.path.basename(f)}")
    print()

    samples = []
    cs = co = mv = lp = cst = lt = None
    for f in files:
        for evt in read_events(f):
            w = evt.which()
            if w == 'carState': cs = evt.carState
            elif w == 'carOutput': co = evt.carOutput
            elif w == 'modelV2': mv = evt.modelV2
            elif w == 'longitudinalPlan': lp = evt.longitudinalPlan
            elif w == 'controlsState': cst = evt.controlsState
            elif w == 'liveTorqueParameters': lt = evt.liveTorqueParameters
            if w == 'carState' and cs is not None:
                v = cs.vEgo; ang = cs.steeringAngleDeg
                a_y = a_y_from_steering(v, ang)
                row = {"v": v, "ang": ang, "a_y": a_y,
                       "steerTorque": cs.steeringTorque, "steerPressed": cs.steeringPressed,
                       "brake": cs.brakePressed, "gas": cs.gasPressed}
                if mv is not None:
                    try:
                        vx = np.array(mv.velocity.x, dtype=np.float64)
                        orz = np.array(mv.orientationRate.z, dtype=np.float64)
                        if len(vx) and len(orz):
                            ayp = np.abs(orz * vx)
                            row["ay_pred_max"] = float(np.max(ayp))
                            row["ay_pred_2s"] = float(np.max(ayp[:15])) if len(ayp) >= 15 else float(np.max(ayp))
                        row["model_desiredAccel"] = float(mv.action.desiredAcceleration)
                        row["model_shouldStop"] = int(bool(mv.action.shouldStop))
                    except Exception: pass
                if lp is not None:
                    try:
                        row["plan_aTarget"] = float(lp.aTarget)
                        row["plan_dangerOverride"] = int(bool(lp.dangerOverrideActive))
                    except Exception: pass
                if cst is not None:
                    try:
                        row["expMode"] = int(bool(cst.experimentalMode))
                        row["forceDecel"] = int(bool(cst.forceDecel))
                    except Exception: pass
                if lt is not None:
                    try:
                        row["torque_factor"] = float(lt.latAccelFactorFiltered)
                        row["torque_friction"] = float(lt.frictionCoefficientFiltered)
                    except Exception: pass
                if co is not None:
                    try:
                        ot = abs(float(co.actuatorsOutput.torque))
                        row["out_torque"] = ot
                        row["steer_sat"] = int(ot >= 0.95)
                    except Exception: pass
                samples.append(row)

    print(f"parsed {len(samples)} carState samples")
    if not samples: return

    # max speed seen
    vmax_all = max(s["v"] for s in samples) * 3.6
    print(f"max speed in drive: {vmax_all:.0f} km/h\n")

    # segment into corners
    corners = []
    cur = None
    for s in samples:
        is_c = s["a_y"] > args.threshold and s["v"] > MIN_SPEED_MPS
        if is_c and cur is None: cur = []
        if is_c: cur.append(s)
        elif cur is not None:
            if len(cur) >= 3: corners.append(cur)
            cur = None
    if cur and len(cur) >= 3: corners.append(cur)

    print(f"found {len(corners)} corner(s) above a_y > {args.threshold} m/s^2\n")
    print("=" * 122)
    print(f"{'#':>2} {'dur':>5} {'v_max':>6} {'v_mean':>6} {'a_y_max':>7} {'a_y_pred':>8} {'mdl_accel':>9} {'plan_aTgt':>9} {'sat%':>5} {'brake%':>6}  notes")
    print("=" * 122)

    import csv as csvmod
    csvf = open(args.csv, "w", newline="") if args.csv else None
    csvw = None
    if csvf:
        flds = ["corner","v_kmh","a_y","a_y_pred_max","model_desiredAccel","plan_aTarget","out_torque","steerPressed","brake"]
        csvw = csvmod.DictWriter(csvf, fieldnames=flds); csvw.writeheader()

    for ci, c in enumerate(corners, 1):
        vmax = max(s["v"] for s in c) * 3.6
        vmean = np.mean([s["v"] for s in c]) * 3.6
        aymax = max(s["a_y"] for s in c)
        aypred = np.mean([s.get("ay_pred_max", 0) for s in c])
        mdl = np.mean([s.get("model_desiredAccel", 0) for s in c])
        plan = min((s.get("plan_aTarget", 0) for s in c), default=0)
        sat = np.mean([s.get("steer_sat", 0) for s in c]) * 100
        brake = np.mean([s.get("brake", 0) for s in c]) * 100
        dur = len(c) * 0.1
        notes = []
        if aypred < aymax * 0.6: notes.append("model UNDER-predicted")
        if any(s.get("steer_sat", 0) for s in c): notes.append("STEER SATURATED")
        if any(s.get("plan_dangerOverride", 0) for s in c): notes.append("danger-override")
        if any(s.get("forceDecel", 0) for s in c): notes.append("forceDecel")
        if any(s.get("brake", 0) for s in c): notes.append("driver-braked")
        print(f"{ci:>2} {dur:>4.1f}s {vmax:>5.0f}km {vmean:>5.0f}km {aymax:>6.2f} {aypred:>7.2f} {mdl:>8.2f} {plan:>8.2f} {sat:>4.0f}% {brake:>5.0f}%  {'; '.join(notes)}")
        if csvw:
            for s in c:
                csvw.writerow({"corner":ci,"v_kmh":f"{s['v']*3.6:.1f}","a_y":f"{s['a_y']:.3f}",
                    "a_y_pred_max":f"{s.get('ay_pred_max',0):.3f}","model_desiredAccel":f"{s.get('model_desiredAccel',0):.3f}",
                    "plan_aTarget":f"{s.get('plan_aTarget',0):.3f}","out_torque":f"{s.get('out_torque',0):.3f}",
                    "steerPressed":int(s.get('steerPressed',0)),"brake":int(s.get('brake',0))})
    print("=" * 122)
    print("legend: a_y_pred = model's predicted peak lat accel | mdl_accel = model desiredAccel (neg=brake) | plan_aTgt = commanded accel (neg=brake)")
    print("        sat% = % of corner with steering torque >= 0.95 (hit 500 cap) | brake% = % driver brake pressed")
    if csvf: csvf.close(); print(f"\nper-sample CSV: {args.csv}")


if __name__ == "__main__":
    main()
