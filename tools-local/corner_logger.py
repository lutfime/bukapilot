#!/usr/bin/env python3
"""
corner_logger.py — live corner instrumentation for the X70.

Subscribes to the running openpilot services and, whenever the car is cornering
beyond a lateral-accel threshold, logs one row per ~100ms to a single CSV.
Captures everything needed to answer "why was this corner taken at this speed?":

  - actual speed, steering, lateral accel (bicycle model, X70-tuned)
  - model's PREDICTED lateral accel (does the model see the corner coming?)
  - model's desiredAcceleration (what does the model want to do?)
  - longitudinalPlan aTarget / shouldStop / dangerOverride (what got commanded)
  - controlsState: experimentalMode, desiredCurvature, forceDecel
  - liveTorqueParameters (torqued state: latAccelFactor, friction, offset)
  - carOutput torque (was steering saturated?)
  - driver input (steeringPressed, brake, gas)

USAGE (on the device, while openpilot is running):
    PYTHONPATH=/data/openpilot /usr/local/venv/bin/python3 \\
        /data/openpilot/tools-local/corner_logger.py

    # optional args:
    #   --threshold 1.5   log corners where a_y > 1.5 m/s^2 (default 1.5)
    #   --output /data/corner_log.csv

Output is a CSV. Stops cleanly on Ctrl-C.
OBSERVATION ONLY — does not change any control behavior.
"""

import argparse
import csv
import math
import os
import signal
import sys
import time

import numpy as np

import cereal.messaging as messaging

# X70 constants (from opendbc_repo/opendbc/car/proton/values.py)
X70_WHEELBASE = 2.67
X70_STEER_RATIO = 15.0

MIN_SPEED_MPS = 8.0          # ignore below ~29 km/h (parking/low-speed)
A_Y_LOG_THRESHOLD = 1.5      # log while lateral accel above this (m/s^2)

FIELDS = [
    "t", "segment_id",
    "vEgo_kmh", "steeringAngleDeg", "steeringTorque", "steeringPressed",
    "brake_pressed", "gas_pressed",
    "a_y_actual",
    "a_y_pred_max", "a_y_pred_2s",
    "model_desiredAccel",
    "model_shouldStop",
    "plan_aTarget", "plan_shouldStop", "plan_dangerOverride", "plan_allowBrake",
    "expMode", "forceDecel", "desiredCurvature",
    "torque_latAccelFactor", "torque_friction", "torque_latAccelOffset", "torque_liveValid",
    "output_torque_norm", "output_steer_saturated",
]


def a_y_from_steering(v_ego, steering_angle_deg, wheelbase=X70_WHEELBASE, ratio=X70_STEER_RATIO):
    if v_ego < 0.5:
        return 0.0
    return (v_ego ** 2) * abs(steering_angle_deg) * math.pi / 180.0 / (ratio * wheelbase)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--threshold", type=float, default=A_Y_LOG_THRESHOLD)
    ap.add_argument("--output", default="/data/corner_log.csv")
    ap.add_argument("--no-buffer-flush", action="store_true")
    args = ap.parse_args()

    sm = messaging.SubManager([
        'carState', 'carOutput', 'modelV2', 'controlsState',
        'longitudinalPlan', 'liveTorqueParameters',
    ])

    write_header = not os.path.exists(args.output) or os.path.getsize(args.output) == 0
    f = open(args.output, "a", newline="")
    w = csv.DictWriter(f, fieldnames=FIELDS)
    if write_header:
        w.writeheader()

    segment_id = 0
    in_corner = False
    corner_start_t = 0.0
    t0 = time.time()
    running = True

    def on_sig(*_):
        nonlocal running
        running = False
    signal.signal(signal.SIGINT, on_sig)
    signal.signal(signal.SIGTERM, on_sig)

    print(f"[corner_logger] logging corners (a_y > {args.threshold} m/s^2) to {args.output}")
    print(f"[corner_logger] X70 wheelbase={X70_WHEELBASE} steerRatio={X70_STEER_RATIO}")
    print("[corner_logger] Ctrl-C to stop. Observation only.")

    while running:
        sm.update()

        cs = sm['carState']
        v_ego = cs.vEgo
        steer = cs.steeringAngleDeg
        a_y = a_y_from_steering(v_ego, steer)

        t = time.time() - t0

        if a_y > args.threshold and v_ego > MIN_SPEED_MPS:
            if not in_corner:
                in_corner = True
                corner_start_t = t
                segment_id += 1
                print(f"[corner_logger] + corner #{segment_id} t={t:.1f}s v={v_ego*3.6:.0f}kmh a_y={a_y:.2f}")
        else:
            if in_corner:
                in_corner = False
                print(f"[corner_logger] - corner #{segment_id} ended ({t - corner_start_t:.1f}s)")

        if not in_corner:
            continue

        row = {k: "" for k in FIELDS}
        row["t"] = f"{t:.2f}"
        row["segment_id"] = segment_id
        row["vEgo_kmh"] = f"{v_ego * 3.6:.1f}"
        row["steeringAngleDeg"] = f"{steer:.1f}"
        row["steeringTorque"] = f"{cs.steeringTorque:.1f}"
        row["steeringPressed"] = int(cs.steeringPressed)
        row["brake_pressed"] = int(cs.brakePressed)
        row["gas_pressed"] = int(cs.gasPressed)
        row["a_y_actual"] = f"{a_y:.3f}"

        try:
            mv = sm['modelV2']
            vx = np.array(mv.velocity.x, dtype=np.float64)
            orz = np.array(mv.orientationRate.z, dtype=np.float64)
            if len(vx) and len(orz):
                ay_pred = np.abs(orz * vx)
                row["a_y_pred_max"] = f"{float(np.max(ay_pred)):.3f}"
                row["a_y_pred_2s"] = f"{float(np.max(ay_pred[:15])):.3f}" if len(ay_pred) >= 15 else f"{float(np.max(ay_pred)):.3f}"
            row["model_desiredAccel"] = f"{float(mv.action.desiredAcceleration):.3f}"
            row["model_shouldStop"] = int(bool(mv.action.shouldStop))
        except Exception:
            pass

        try:
            lp = sm['longitudinalPlan']
            row["plan_aTarget"] = f"{float(lp.aTarget):.3f}"
            row["plan_shouldStop"] = int(bool(lp.shouldStop))
            row["plan_dangerOverride"] = int(bool(lp.dangerOverrideActive))
            row["plan_allowBrake"] = int(bool(lp.allowBrake))
        except Exception:
            pass

        try:
            cst = sm['controlsState']
            row["expMode"] = int(bool(cst.experimentalMode))
            row["forceDecel"] = int(bool(cst.forceDecel))
            row["desiredCurvature"] = f"{float(cst.desiredCurvature):.5f}"
        except Exception:
            pass

        try:
            lt = sm['liveTorqueParameters']
            row["torque_latAccelFactor"] = f"{float(lt.latAccelFactorFiltered):.4f}"
            row["torque_friction"] = f"{float(lt.frictionCoefficientFiltered):.4f}"
            row["torque_latAccelOffset"] = f"{float(lt.latAccelOffsetFiltered):.4f}"
            row["torque_liveValid"] = int(bool(lt.liveValid))
        except Exception:
            pass

        try:
            co = sm['carOutput']
            ot = abs(float(co.actuatorsOutput.torque))
            row["output_torque_norm"] = f"{ot:.3f}"
            row["output_steer_saturated"] = int(ot >= 0.95)
        except Exception:
            pass

        w.writerow(row)
        if not args.no_buffer_flush:
            f.flush()

    f.flush()
    f.close()
    print(f"[corner_logger] stopped. {segment_id} corners logged to {args.output}")


if __name__ == "__main__":
    main()
