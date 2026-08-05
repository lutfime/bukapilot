#!/usr/bin/env python3
"""Deduce torque-controller SEED params (latAccelFactor, friction) for the Proton X70
from a saved log, replicating locationd/torqued.py's fit.

torqued fits:  lateral_accel  vs  steer_output_torque
  - lateral_accel = vEgo * yaw_rate(IMU)  [I fall back to kinematic from steering angle]
  - steer torque  = carOutput.actuatorsOutput.torque [I fall back to carControl.actuators.torque, normalized]
  - filters: latActive, vEgo>15 m/s, |lataccel|<=1, |steer|>0.02, no driver steering override
slope = latAccelFactor (m/s^2 per unit steer command); friction ~= 1.5 * residual std in steer-units.

X70 geometry: wheelbase=2.67, steerRatio=15.0 (opendbc_repo proton/values.py).
This is a SEED only -- torqued refines it online (clips learned value to +/-30% of seed).
"""
import sys, math, numpy as np
sys.path.insert(0, "/Users/WanLutfi/Documents/Xcode/Kommu Lutfi")
from cereal import log as capnp_log
EV = capnp_log.Event

WHEELBASE = 2.67
STEER_RATIO = 15.0
MIN_VEL = 15.0
LAT_ACC_MAX = 1.0
STEER_MIN = 0.02

def parse(path):
  with open(path, "rb") as f: return list(EV.read_multiple_bytes(f.read()))
def safe(o, *p, default=float("nan")):
  cur = o
  for x in p:
    try: cur = getattr(cur, x)
    except Exception: return default
  return cur

def main():
  path = sys.argv[1]
  ev = parse(path)
  cc_t, cmd, lat = [], [], []
  cs_t, vEgo, ang, pressed = [], [], [], []
  lp_t, yaw = [], []
  has_livepose = False
  for e in ev:
    try: w = e.which()
    except Exception: continue
    t = (e.logMonoTime or 0)/1e9
    if w == "carControl":
      cc_t.append(t); cmd.append(float(safe(e.carControl.actuators, "torque"))); lat.append(bool(safe(e.carControl, "latActive")))
    elif w == "carState":
      cs = e.carState
      cs_t.append(t); vEgo.append(float(safe(cs, "vEgo"))); ang.append(float(safe(cs, "steeringAngleDeg")))
      pressed.append(bool(safe(cs, "steeringPressed")))
    elif w == "livePose":
      y = safe(e.livePose, "angularVelocity", "yaw", default=float("nan"))
      if not (isinstance(y, float) and math.isnan(y)):
        lp_t.append(t); yaw.append(float(y)); has_livepose = True
  cc_t=np.array(cc_t); cmd=np.array(cmd); lat=np.array(lat,dtype=bool)
  cs_t=np.array(cs_t); vEgo=np.array(vEgo); ang=np.array(ang); pressed=np.array(pressed,dtype=bool)
  if len(cc_t) < 200: print("too few frames"); return

  # align onto carControl timeline
  vEgo_c = np.interp(cc_t, cs_t, vEgo)
  ang_c  = np.interp(cc_t, cs_t, ang)
  pressed_c = np.interp(cc_t, cs_t, pressed.astype(float)) > 0.5
  # lateral accel
  if has_livepose:
    lp_t=np.array(lp_t); yaw=np.array(yaw)
    latacc_imu = np.interp(cc_t, lp_t, yaw) * vEgo_c
    src = "livePose yaw_rate * vEgo (matches torqued)"
  else:
    curv = np.tan(np.radians(ang_c/STEER_RATIO))/WHEELBASE   # small-angle kinematic curvature
    latacc_imu = vEgo_c**2 * curv
    src = "KINEMATIC approx (vEgo^2 * steerAngle/steerRatio/wheelbase) -- no IMU yaw in log"

  steer = cmd  # normalized command (torqued uses actuatorsOutput.torque ~ same scale)
  # torqued filters
  ok = lat & (vEgo_c > MIN_VEL) & (np.abs(latacc_imu) <= LAT_ACC_MAX) & (np.abs(steer) > STEER_MIN) & (~pressed_c) & np.isfinite(latacc_imu)
  x = steer[ok]; y = latacc_imu[ok]
  print(f"file: {path}")
  print(f"lateral accel source: {src}")
  print(f"qualifying points (engaged, vEgo>{MIN_VEL} m/s, |lataccel|<={LAT_ACC_MAX}, |cmd|>{STEER_MIN}, no driver): {len(x)}")
  if len(x) < 30:
    print("  -> too few high-speed points for a confident fit (need more highway driving). Seed will be rough.")
  if len(x) < 5:
    print("  -> not enough; aborting."); return

  # OLS slope (y ~ x): latAccelFactor = d(lataccel)/d(steer)
  A = np.vstack([x, np.ones_like(x)]).T
  slope, offset = np.linalg.lstsq(A, y, rcond=None)[0]
  resid = y - (slope*x + offset)
  r2 = 1 - np.var(resid)/np.var(y) if np.var(y) > 1e-9 else 0
  # total least squares (torqued's method), cross-check
  pts = np.vstack([x, y])
  _, _, v = np.linalg.svd(pts - pts.mean(1, keepdims=True), full_matrices=False)
  tls_slope = -v[0,1]/v[1,1] if abs(v[1,1]) > 1e-9 else float("nan")
  # friction: 1.5 * std of residual in STEER units
  friction = 1.5 * np.std(resid/slope) if abs(slope) > 1e-9 else float("nan")

  print(f"\n=== DEDUCED X70 torque SEED (latAccelFactor = d(lateralAccel)/d(steerCmd)) ===")
  print(f"  latAccelFactor (OLS)  = {slope:.4f}   (TLS cross-check: {tls_slope:.4f})")
  print(f"  latAccelOffset        = {offset:.4f}")
  print(f"  friction (~1.5*resid) = {friction:.4f}")
  print(f"  fit: R^2={r2:.2f}  n={len(x)}  steer cmd range [{x.min():.3f},{x.max():.3f}]  lataccel range [{y.min():.2f},{y.max():.2f}] m/s^2")
  # direction sanity
  print(f"  sign: steer vs lataccel correlation = {np.corrcoef(x,y)[0,1]:+.2f} (expect positive if conventions match; use |slope|)")
  print(f"\n  => suggested SEED: latAccelFactor={abs(slope):.3f}, friction={friction:.3f}, latAccelOffset=0.0, steeringAngleDeadzoneDeg=0.0")

if __name__ == "__main__":
  main()
