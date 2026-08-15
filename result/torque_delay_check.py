#!/usr/bin/env python3
"""Today's drive recheck: (1) max steer torque usage vs STEER_MAX=550,
(2) steer actuator delay (steerActuatorDelay, currently 0.17),
(3) longitudinal actuator delay (longitudinalActuatorDelay, currently 0.45).

Method: sliding 10s windows, per-window cross-correlation lag between command
and response; report distribution (robust to mixed conditions).
Torque cmd is PID-normalized [-1,1] -> CAN units = cmd * 550.
"""
import sys, glob, re, numpy as np
sys.path.insert(0, "/Users/WanLutfi/Documents/Xcode/Kommu")
from result.drive_analysis import parse, safe
nan = float("nan")

STEER_MAX = 550.0


def segnum(p):
  m = re.search(r'--(\d+)\.rlog\.zst$', p)
  return int(m.group(1)) if m else 0

def main():
  paths = sorted(glob.glob(sys.argv[1] if len(sys.argv)>1 else 'result/drives/2026-08-14--00-45-49--*.rlog.zst'), key=segnum)
  ct=[]; cmd=[]; acc=[]; lat=[]; lng=[]
  st=[]; ang=[]; eps=[]; aego=[]
  for p in paths:
    for e in parse(p):
      w=e.which(); tt=(e.logMonoTime or 0)/1e9
      if w=='carControl':
        a=e.carControl.actuators
        ct.append(tt); cmd.append(float(safe(a,'torque'))); acc.append(float(safe(a,'accel')))
        lat.append(bool(safe(e.carControl,'latActive',d=False))); lng.append(bool(safe(e.carControl,'longActive',d=False)))
      elif w=='carState':
        cs=e.carState
        st.append(tt); ang.append(float(safe(cs,'steeringAngleDeg'))); eps.append(float(safe(cs,'steeringTorqueEps')))
        aego.append(float(safe(cs,'aEgo')))
  ct=np.array(ct); cmd=np.array(cmd); acc=np.array(acc); lat=np.array(lat,bool); lng=np.array(lng,bool)
  st=np.array(st)
  # monotonic guard: drop any events with non-increasing time (segment overlap protection)
  keep=np.concatenate(([True], np.diff(ct)>0))
  ct=ct[keep]; cmd=cmd[keep]; acc=acc[keep]; lat=lat[keep]; lng=lng[keep]
  ang=np.array(ang); eps=np.array(eps); aego=np.array(aego)
  keep_s=np.concatenate(([True], np.diff(st)>0))
  st=st[keep_s]; ang=ang[keep_s]; eps=eps[keep_s]; aego=aego[keep_s]
  ang=np.interp(ct,st,ang); eps=np.interp(ct,st,eps); aego=np.interp(ct,st,aego)
  dt=float(np.median(np.diff(ct)))
  # uniform grid
  gu=np.arange(ct[0], ct[-1], dt)
  cmd_u=np.interp(gu,ct,cmd); acc_u=np.interp(gu,ct,acc); ang_u=np.interp(gu,ct,ang)
  eps_u=np.interp(gu,ct,eps); aego_u=np.interp(gu,ct,aego)
  lat_u=np.interp(gu,ct,lat)>0.5; lng_u=np.interp(gu,ct,lng)>0.5
  print(f"=== {len(paths)} segs, {len(gu)} frames @ {1/dt:.0f}Hz, engaged={100*lat_u.mean():.0f}% ===\n")

  # ---------- 1) MAX TORQUE USAGE ----------
  m = lat_u & np.isfinite(cmd_u)
  c = np.abs(cmd_u[m])
  print("=== STEER TORQUE USAGE (PID-normalized; CAN units = cmd*550) ===")
  print(f"  max |cmd| = {c.max():.3f}  -> {c.max()*STEER_MAX:.0f} CAN units (limit {STEER_MAX:.0f})")
  for q in (50,90,99,99.9):
    print(f"  p{q:<4} = {np.percentile(c,q):.3f} -> {np.percentile(c,q)*STEER_MAX:.0f} units")
  for thr,lab in ((0.90,'>90%'),(0.95,'>95%'),(0.99,'>99%'),(0.999,'~railing')):
    print(f"  engaged frames {lab:9s}: {100*np.mean(c>thr if thr<1 else c>=thr):.2f}%")
  # sustained near-limit (the EPS authority-drop concern): runs >2s above 0.9
  above = (c.max()>0)
  runs=[]; n=0
  for v in (np.abs(cmd_u)*lat_u):
    if v>0.90: n+=1
    else:
      if n>0: runs.append(n)
      n=0
  if runs:
    runs=np.array(runs)*dt
    print(f"  near-limit(>0.90) runs: n={len(runs)}, longest={runs.max():.1f}s (>2s sustained = EPS authority-drop risk)")
  else:
    print("  near-limit(>0.90) runs: NONE — never sustained near limit")
  print()

  # ---------- cross-correlation lag helper ----------
  def lag_dist(x, y, mask, win_s=10.0, min_std=1e-3, max_lag_s=1.2):
    W=int(win_s/dt); L=int(max_lag_s/dt)
    lags=[]
    i=0
    while i+W<=len(x):
      sl=slice(i,i+W)
      mk=mask[sl]
      if mk.mean()>0.8 and np.std(x[sl])>min_std and np.std(y[sl])>min_std:
        xs=x[sl]-np.mean(x[sl]); ys=y[sl]-np.mean(y[sl])
        xs=np.where(mk,xs,0.0)  # zero out disengaged
        best=None; bestr=-2
        for tau in range(-L,L+1):
          if tau>=0: a=xs[:len(xs)-tau or None]; b=ys[tau:]
          else: a=xs[-tau:]; b=ys[:len(ys)+tau]
          a=a[:len(b)]; b=b[:len(a)]
          r=np.dot(a,b)/ (np.linalg.norm(a)*np.linalg.norm(b)+1e-12)
          if r>bestr: bestr=r; best=tau
        if best is not None and bestr>0.3: lags.append(best*dt)
      i+=W
    return np.array(lags)

  # ---------- 2) STEER DELAY ----------
  print("=== STEER ACTUATOR DELAY (current 0.17s) ===")
  # cmd torque -> EPS torque response (fast path)
  l1=lag_dist(cmd_u, eps_u, lat_u)
  if len(l1)>3:
    print(f"  cmd->EPS torque:    p25={np.percentile(l1,25)*1000:.0f}ms p50={np.median(l1)*1000:.0f}ms p75={np.percentile(l1,75)*1000:.0f}ms (n={len(l1)})")
  # cmd torque -> steering angle (integrator, larger)
  l2=lag_dist(cmd_u, ang_u, lat_u)
  if len(l2)>3:
    print(f"  cmd->steer ANGLE:   p25={np.percentile(l2,25)*1000:.0f}ms p50={np.median(l2)*1000:.0f}ms p75={np.percentile(l2,75)*1000:.0f}ms (n={len(l2)})")
  print()

  # ---------- 3) LONGITUDINAL DELAY ----------
  print("=== LONGITUDINAL ACTUATOR DELAY (current 0.45s) ===")
  l3=lag_dist(acc_u, aego_u, lng_u)
  if len(l3)>3:
    print(f"  accel cmd->aEgo:    p25={np.percentile(l3,25)*1000:.0f}ms p50={np.median(l3)*1000:.0f}ms p75={np.percentile(l3,75)*1000:.0f}ms (n={len(l3)})")
  else:
    print(f"  (insufficient longActive windows: n={len(l3)})")
  print()

if __name__=='__main__': main()
