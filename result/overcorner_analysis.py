#!/usr/bin/env python3
"""Overcorner analysis for Proton X70 (PID lateral, kf=0.00015).

Question: at corners does the car turn MORE than the path needs?
Compares ACTUAL curvature (controlsState.curvature) vs DESIRED (desiredCurvature).
  - actual > desired  => PID/car overshoots OP's own command (tuning: kp/kf too hot)
  - actual ~= desired  => tracking fine; any "over" feel = model desired too sharp (not PID)
  - actual < desired  => understeer / lag
"""
import sys, glob, numpy as np
sys.path.insert(0, "/Users/WanLutfi/Documents/Xcode/Kommu")
from result.drive_analysis import parse, safe
nan = float("nan")

def collect(paths):
  cols = dict(T=[],lat=[],ve=[],ang=[],cmd=[],ac=[],dc=[],p=[],i=[],f=[],o=[],e=[])
  for p in paths:
    cT=[];cac=[];cdc=[]   # controlsState timeline
    for e in parse(p):
      w=e.which(); tt=(e.logMonoTime or 0)/1e9
      if w=='controlsState':
        c=e.controlsState
        cT.append(tt); cac.append(float(safe(c,'curvature'))); cdc.append(float(safe(c,'desiredCurvature')))
        lc=safe(c,'lateralControlState')
        cols['p'].append(float(safe(lc,'pidState','p'))); cols['i'].append(float(safe(lc,'pidState','i')))
        cols['f'].append(float(safe(lc,'pidState','f'))); cols['o'].append(float(safe(lc,'pidState','output')))
        cols['e'].append(float(safe(lc,'pidState','error')))
      elif w=='carState':
        cs=e.carState
        cols['T'].append(tt); cols['ve'].append(float(safe(cs,'vEgo'))); cols['ang'].append(float(safe(cs,'steeringAngleDeg')))
      elif w=='carControl':
        cols['lat'].append((tt,bool(safe(e.carControl,'latActive',d=False)))); cols['cmd'].append((tt,float(safe(e.carControl.actuators,'torque'))))
  # controlsState arrays (p/i/f/o/e align with cT)
  cT=np.array(cT); cac=np.array(cac); cdc=np.array(cdc)
  for k in ('p','i','f','o','e'): cols[k]=np.array(cols[k])
  # carState align to controlsState time
  T=np.array(cols['T']); ve=np.interp(cT,T,cols['ve']); ang=np.interp(cT,T,cols['ang'])
  lat_t=np.array([x[0] for x in cols['lat']]); lat_v=np.array([x[1] for x in cols['lat']],bool)
  cmd_t=np.array([x[0] for x in cols['cmd']]); cmd_v=np.array([x[1] for x in cols['cmd']])
  lat=np.interp(cT,lat_t,lat_v).astype(bool); cmd=np.interp(cT,cmd_t,cmd_v)
  return cT,lat,ve,ang,cmd,cac,cdc,cols

def main():
  paths=sorted(glob.glob(sys.argv[1] if len(sys.argv)>1 else 'result/drives/2026-08-14--00-45-49--*.rlog.zst'))
  T,lat,ve,ang,cmd,ac,dc,cols=collect(paths)
  dt=float(np.median(np.diff(T)))
  alat = ac*ve*ve   # actual lateral accel
  dlat = dc*ve*ve   # desired lateral accel
  print(f"=== {len(paths)} segs, {len(T)} controlsState frames @ {1/dt:.0f}Hz, engaged={100*lat.mean():.0f}% ===")
  print(f"vEgo engaged: p50={np.median(ve[lat]):.1f} max={np.max(ve[lat]):.1f} m/s\n")

  # corner mask: engaged, noticeable desired curvature, moving
  corn = lat & (np.abs(dc)>0.004) & (ve>4)
  print(f"corner frames (|desCurv|>0.004, engaged, v>4): {100*corn.mean():.1f}% of time\n")

  # ---- GLOBAL tracking: actual vs desired at corners ----
  print("=== CURVATURE TRACKING at corners (actual vs desired) ===")
  m=corn
  # sign-aware ratio (both same sign usually); use abs
  ov = np.abs(ac[m]) - np.abs(dc[m])    # >0 = actual exceeds desired = OVERCORNER
  print(f"  actual-desired |curvature| residual: mean={np.mean(ov):+.5f} p50={np.median(ov):+.5f}")
  print(f"  pct corner-frames where |actual|>|desired|: {100*np.mean(ov>0):.0f}%   (overcorner)")
  print(f"  pct where |actual|<|desired|: {100*np.mean(ov<0):.0f}%   (under/lag)")
  # lateral accel overshoot
  ova = np.abs(alat[m]) - np.abs(dlat[m])
  print(f"  lateral-accel overshoot (actual-desired): mean={np.mean(ova):+.3f} m/s^2  p90={np.percentile(ova,90):+.3f}")
  print(f"  desired lat-accel at corners: p50={np.median(np.abs(dlat[m])):.2f} p90={np.percentile(np.abs(dlat[m]),90):.2f} m/s^2\n")

  # ---- corner EVENTS: peak overshoot per corner ----
  # detect contiguous corner runs
  edges=np.where(np.diff(np.r_[0,corn.astype(int),0])!=0)[0].reshape(-1,2)
  ev=[]
  for a,b in edges:
    if b-a < 30: continue  # <0.3s skip
    s=slice(a,b)
    pk_d=np.max(np.abs(dc[s])); pk_a=np.max(np.abs(ac[s]))
    # peak actual within corner (allow small lead/lag window)
    ratio=pk_a/pk_d if pk_d>1e-5 else nan
    # error sign at this corner (error = desired - actual typically); sample mid-corner
    err_mid=cols['e'][(a+b)//2]
    f_mid=cols['f'][(a+b)//2]
    ev.append((ratio,pk_d,pk_a,err_mid,f_mid,ve[(a+b)//2]))
  ev=np.array(ev)
  print(f"=== {len(ev)} CORNER EVENTS ===")
  r=ev[:,0]
  print(f"  overshoot ratio peak|actual|/peak|desired|: p50={np.nanmedian(r):.2f} p75={np.nanpercentile(r,75):.2f} p90={np.nanpercentile(r,90):.2f}")
  print(f"  corners with ratio>1.10 (>10% over): {100*np.nanmean(r>1.10):.0f}%   ratio>1.0: {100*np.nanmean(r>1.0):.0f}%")
  print(f"  corner peak |desired curv|: p50={np.nanmedian(ev[:,1]):.4f} p90={np.nanpercentile(ev[:,1],90):.4f}")
  print(f"  f-term at corner mid: p50={np.nanmedian(np.abs(ev[:,4])):.3f} p90={np.nanpercentile(np.abs(ev[:,4]),90):.3f}\n")

  # ---- triggered average around corner ENTRY ----
  # entry = first frame a fresh corner starts (rising edge of corn)
  rises=np.where((~corn[:-1])&(corn[1:]))[0]+1
  W=int(2.0/dt)  # +-2s window
  A=D=CMD=F=P=np.zeros(0)
  for r0 in rises:
    if r0<W or r0+W>=len(T): continue
    A=np.append(A,ac[r0-W:r0+W]); D=np.append(D,dc[r0-W:r0+W])
    CMD=np.append(CMD,cmd[r0-W:r0+W]); F=np.append(F,cols['f'][r0-W:r0+W]); P=np.append(P,cols['p'][r0-W:r0+W])
  A=A.reshape(-1,2*W); D=D.reshape(-1,2*W); CMD=CMD.reshape(-1,2*W); F=F.reshape(-1,2*W); P=P.reshape(-1,2*W)
  # sign-normalize so left/right corners add
  sgn=np.sign(D[:,W])
  A=A*sgn[:,None]; D=D*sgn[:,None]; CMD=CMD*sgn[:,None]; F=F*sgn[:,None]; P=P*sgn[:,None]
  ta=np.arange(-W,W)/dt*dt
  print(f"=== TRIGGERED AVG at corner ENTRY ({A.shape[0]} entries, sign-normalized) ===")
  for frac in (0.0,0.3,0.6,0.9,1.2,1.5):
    k=min(int(frac/dt),2*W-1); kk=k+1
    print(f"  t={ta[kk]:+.1f}s: desCurv={np.median(D[:,kk]):+.4f} actCurv={np.median(A[:,kk]):+.4f} "
          f"(|act|>|des|? {'OVER' if abs(np.median(A[:,kk]))>abs(np.median(D[:,kk]))+1e-5 else 'under/lag'}) "
          f"cmd={np.median(CMD[:,kk]):+.3f} f={np.median(F[:,kk]):+.3f} p={np.median(P[:,kk]):+.3f}")
  # entry overshoot: does actual peak ABOVE desired in first 1.5s?
  win=slice(W, W+int(1.5/dt))
  over_entry = np.mean(np.abs(A[:,win]) > np.abs(D[:,win]) + 0.0008, axis=1)
  print(f"  entry (0-1.5s): corners where |actual| overshoots |desired|: {100*np.mean(over_entry>0.3):.0f}% (of entries)\n")

  # ---- steer command reversal at entry (overshoot correction signature) ----
  # in first 1.0s after entry, does cmd sign flip or peak-then-decay?
  flips=0; n=0
  for r0 in rises:
    if r0<2 or r0+int(1.0/dt)>=len(T): continue
    n+=1
    seg=cmd[r0:r0+int(1.0/dt)]
    pk=np.argmax(np.abs(seg))
    if pk< len(seg)-3 and abs(seg[pk])>0.05:
      after=seg[pk:]
      if (np.max(after)-np.min(after))>0.04 and np.sign(np.mean(seg[pk:]))!=np.sign(seg[pk]):
        flips+=1
  print(f"=== ENTRY steer-cmd peak-then-reversal (overshoot correction): {flips}/{n} entries ({100*flips/max(n,1):.0f}%) ===")

if __name__=='__main__': main()
