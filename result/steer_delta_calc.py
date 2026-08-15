#!/usr/bin/env python3
"""STEER_DELTA exact-number calculation from today's drive.

Questions:
1. How fast does controlsd WANT to move the command (desired rate, CAN units/frame)?
   -> how often does STEER_DELTA=10 clip it (carOutput vs carControl comparison)?
2. What is the EPS's real slew capability (|d(steeringTorqueEps)|/frame)?
3. Any steer faults (the actual 'release' mechanism) in this drive?
"""
import sys, glob, re, numpy as np
sys.path.insert(0, "/Users/WanLutfi/Documents/Xcode/Kommu")
from result.drive_analysis import parse, safe
nan = float("nan")

def segnum(p):
  m = re.search(r'--(\d+)\.rlog\.zst$', p)
  return int(m.group(1)) if m else 0

STEER_MAX = 550.0
DELTA = 10.0  # current STEER_DELTA_UP/DOWN

def main():
  paths = sorted(glob.glob('result/drives/2026-08-14--00-45-49--*.rlog.zst'), key=segnum)
  ct=[]; des=[]; lat=[]
  ot=[]; sent=[]
  st=[]; eps=[]
  faults=0; n_onroad=0
  for p in paths:
    for e in parse(p):
      w=e.which(); tt=(e.logMonoTime or 0)/1e9
      if w=='carControl':
        ct.append(tt); des.append(float(safe(e.carControl.actuators,'torque')))
        lat.append(bool(safe(e.carControl,'latActive',d=False)))
      elif w=='carOutput':
        ot.append(tt); sent.append(float(safe(e.carOutput,'actuatorsOutput','torque')))
      elif w=='carState':
        st.append(tt); eps.append(float(safe(e.carState,'steeringTorqueEps')))
      elif w=='onroadEvents':
        for evx in (e.onroadEvents.events if hasattr(e.onroadEvents,'events') else []):
          n_onroad+=1
          t=str(evx.type) if hasattr(evx,'type') else str(evx)
          if 'teer' in t or 'fault' in t.lower(): faults+=1
  ct=np.array(ct); des=np.array(des); lat=np.array(lat,bool)
  ot=np.array(ot); sent=np.array(sent)
  st=np.array(st); eps=np.array(eps)
  k=np.concatenate(([True],np.diff(ct)>0)); ct=ct[k]; des=des[k]; lat=lat[k]
  ko=np.concatenate(([True],np.diff(ot)>0)); ot=ot[ko]; sent=sent[ko]
  ks=np.concatenate(([True],np.diff(st)>0)); st=st[ks]; eps=eps[ks]
  eps_i=np.interp(ct,st,eps)
  sent_i=np.interp(ct,ot,sent)
  dt=np.median(np.diff(ct))
  print(f"=== frames={len(ct)} @ {1/dt:.0f}Hz, engaged={100*lat.mean():.0f}% ===\n")

  # ---- 1) DESIRED vs SENT rate (CAN units per frame) ----
  m = lat & np.isfinite(des)
  dr = np.abs(np.diff(des))*STEER_MAX   # desired rate per frame
  mr = m[1:] & m[:-1]                   # both frames engaged
  dr_e = dr[mr]
  print("=== COMMAND RATE (CAN units/frame) — desired (controlsd) ===")
  print(f"  desired |d/dt|: p50={np.percentile(dr_e,50):.2f} p90={np.percentile(dr_e,90):.2f} "
        f"p95={np.percentile(dr_e,95):.2f} p99={np.percentile(dr_e,99):.2f} max={dr_e.max():.1f}")
  print(f"  desired frames exceeding DELTA={DELTA:.0f}: {100*np.mean(dr_e>DELTA):.2f}%  (>{2*DELTA:.0f}: {100*np.mean(dr_e>2*DELTA):.2f}%)")
  # sent rate (post-safety)
  sr = np.abs(np.diff(sent_i))*STEER_MAX
  sr_e = sr[mr & np.isfinite(sent_i[1:]) & np.isfinite(sent_i[:-1])]
  if len(sr_e):
    print(f"  SENT (post-safety) |d/dt|: p99={np.percentile(sr_e,99):.2f} max={sr_e.max():.1f} (should cap ~{DELTA:.0f})")
  # clipping: desired wants > delta but sent moves exactly delta
  both = mr & np.isfinite(sr) & np.isfinite(dr)
  clipped = both & (dr > DELTA*1.05) & (np.abs(sr - DELTA) < 0.6)
  print(f"  frames where safety CLIPPED the rate: {100*np.mean(clipped):.2f}% of engaged transitions")
  print()

  # ---- 2) EPS real slew capability ----
  print("=== EPS REAL SLEW (|d(steeringTorqueEps)| per frame, engaged) ===")
  er = np.abs(np.diff(eps_i))[mr]
  print(f"  p50={np.percentile(er,50):.2f} p90={np.percentile(er,90):.2f} p95={np.percentile(er,95):.2f} "
        f"p99={np.percentile(er,99):.2f} max={er.max():.1f} units/frame")
  print(f"  (prev measurement was p95~9 -> chose DELTA=10)")
  print()

  # ---- 3) steer faults ----
  print(f"=== onroadEvents: {n_onroad} total, steer/fault events: {faults} ===")

if __name__=='__main__': main()
