#!/usr/bin/env python3
"""Full-rate PID analysis on engaged X70 rlogs.
Lateral: steer step-size/jerk, PID term balance (p/i/f/output), tracking, oscillation.
Longitudinal: accel tracking + jerk.
Signals use the angle-based cereal schema (steeringAngleDeg actuators)."""
import sys, glob, os, numpy as np
sys.path.insert(0, "/Users/WanLutfi/Documents/Xcode/Kommu")
from cereal import log as capnp_log
import zstandard as zstd
EV = capnp_log.Event

def parse(p):
  data = open(p,"rb").read()
  try: return list(EV.read_multiple_bytes(data))
  except Exception: pass
  r = zstd.ZstdDecompressor().stream_reader(data)
  return list(EV.read_multiple_bytes(r.read()))

def G(o,*p):
  c=o
  for x in p:
    try: c=getattr(c,x)
    except Exception: return None
  return c

BUCK = [(0,5),(5,10),(10,15),(15,20),(20,25),(25,35)]
BN = [f"{a}-{b}" for a,b in BUCK]

def bucket(v):
  for i,(a,b) in enumerate(BUCK):
    if a<=v<b: return i
  return None

def pct(x,q):
  x=np.asarray(x); x=x[np.isfinite(x)]
  return np.percentile(x,q) if len(x) else float('nan')

def analyze(rlog):
  ev = parse(rlog)
  # controlsState ~ full rate: pidState internals + desired curvature
  t=[]; p=[]; i=[]; f=[]; out=[]; ae=[]; sa_des=[]; curv_des=[]; curv_act=[]; sat=0; active=0; n=0
  # carState: actual steering, vEgo, aEgo
  ts=[]; sa_act=[]; srate=[]; storque=[]; vego=[]; aego=[]
  # carControl: commanded actuators (steeringAngleDeg, accel, gas, brake)
  tc=[]; cmd_steer=[]; cmd_accel=[]; gas=[]; brake=[]; lcs=[]
  for e in ev:
    w=e.which()
    if w=="controlsState":
      n+=1
      t.append(e.logMonoTime)
      ps=G(e.controlsState,"lateralControlState","pidState")
      if ps is not None:
        p.append(G(ps,"p") or 0.0); i.append(G(ps,"i") or 0.0); f.append(G(ps,"f") or 0.0)
        out.append(G(ps,"output") or 0.0); ae.append(G(ps,"angleError") or 0.0)
        sa_des.append(G(ps,"steeringAngleDeg") or 0.0)
        if G(ps,"saturated"): sat+=1
        if G(ps,"active"): active+=1
      curv_des.append(G(e.controlsState,"desiredCurvature") or 0.0)
      curv_act.append(G(e.controlsState,"curvature") or 0.0)
      lcs.append(str(G(e.controlsState,"longControlState")))
    elif w=="carState":
      ts.append(e.logMonoTime)
      sa_act.append(G(e.carState,"steeringAngleDeg") or 0.0)
      srate.append(G(e.carState,"steeringRateDeg") or 0.0)
      storque.append(G(e.carState,"steeringTorque") or 0.0)
      vego.append(G(e.carState,"vEgo") or 0.0)
      aego.append(G(e.carState,"aEgo") if G(e.carState,"aEgo") is not None else float('nan'))
    elif w=="carControl":
      tc.append(e.logMonoTime)
      act=G(e.carControl,"actuators")
      if act is not None:
        cmd_steer.append(G(act,"steeringAngleDeg") or 0.0)
        cmd_accel.append(G(act,"accel") if G(act,"accel") is not None else float('nan'))
        gas.append(G(act,"gas") or 0.0); brake.append(G(act,"brake") or 0.0)
  t=np.array(t,float); ts=np.array(ts,float); tc=np.array(tc,float)
  # PID tick dt
  dt = np.diff(t)/1e9 if len(t)>1 else np.array([0.02])
  rate = 1.0/np.median(dt) if len(dt) and np.median(dt)>0 else 0.0
  out=np.array(out); p=np.array(p); i=np.array(i); f=np.array(f); ae=np.array(ae); sa_des=np.array(sa_des)
  sa_act=np.array(sa_act); vego=np.array(vego); aego=np.array(aego)
  cmd_steer=np.array(cmd_steer); cmd_accel=np.array(cmd_accel)
  curv_des=np.array(curv_des); curv_act=np.array(curv_act)

  res = dict(seg=os.path.basename(os.path.dirname(rlog)), rate=rate, n=n, sat=sat, active=active,
             t=t, p=p,i=i,f=f,out=out,ae=ae, sa_des=sa_des, curv_des=curv_des,curv_act=curv_act,
             ts=ts, sa_act=sa_act, vego=vego, aego=aego, tc=tc, cmd_steer=cmd_steer, cmd_accel=cmd_accel)
  return res

def step_stats(vals, t):
  """per-tick abs delta, with rate guard"""
  if len(vals)<2: return (0,0,0,0,0)
  d=np.abs(np.diff(vals))
  return (np.mean(d), np.median(d), pct(d,95), np.max(d), np.std(d))

if __name__=="__main__":
  segs = sorted(glob.glob("result/drives/2026-08-08--1*/rlog.zst"))
  print(f"Analyzing {len(segs)} engaged rlog segments\n")
  all_steps=[]; all_step_v=[]   # steer command step vs speed
  all_ae=[]; all_ae_v=[]; all_osc=[]; all_osc_v=[]
  all_accelerr=[]; all_jerk=[]
  for s in segs:
    r=analyze(s)
    vmean=np.mean(r['vego']) if len(r['vego']) else 0
    # steer command step (degrees/tick) from pidState.steeringAngleDeg
    m,med,p95,mx,sd = step_stats(r['sa_des'], r['t'])
    # actual steer step
    am,amed,ap95,amx,asd = step_stats(r['sa_act'], r['ts'])
    # angle error
    ae_abs = np.abs(r['ae'])
    # output term balance
    def rms(x): return float(np.sqrt(np.mean(np.asarray(x)**2))) if len(x) else 0
    # oscillation: sign changes of output per sec
    if len(r['out'])>2:
      sc=np.sum(np.diff(np.sign(r['out']))!=0); dur=(r['t'][-1]-r['t'][0])/1e9
      osc=sc/max(dur,0.1)
    else: osc=0
    # longitudinal accel tracking: align cmd_accel to aego by nearest time
    cmd_err=np.nan; jerk=np.nan
    if len(r['cmd_accel']) and len(r['aego']) and len(r['tc']) and len(r['ts']):
      ca=r['cmd_accel']; ca=ca[np.isfinite(ca)]
      ae2=r['aego']; ae2=ae2[np.isfinite(ae2)]
      # jerk = daego/dt
      if len(r['ts'])>2:
        dts=np.diff(r['ts'])/1e9; da=np.diff(ae2)
        dd=da/dts; dd=dd[(dts>0.01)&(dts<0.5)]; jerk=np.std(dd) if len(dd) else np.nan
    print(f"== {r['seg']}  vEgo~{vmean:4.1f} m/s  rate={r['rate']:.0f}Hz  sat={r['sat']} ==")
    print(f"   STEER CMD step/tick (deg): mean={m:5.2f} med={med:5.2f} p95={p95:5.2f} max={mx:6.2f}")
    print(f"   STEER ACT step/tick (deg): mean={am:5.2f} p95={ap95:5.2f} max={amx:6.2f}")
    print(f"   angleError (deg): mean|e|={np.mean(ae_abs):5.2f} p95|e|={pct(ae_abs,95):5.2f} max|e|={np.max(ae_abs):6.2f}")
    print(f"   PID rms: p={rms(r['p']):6.3f} i={rms(r['i']):6.3f} f={rms(r['f']):6.3f} out={rms(r['out']):6.3f}  | f/out={rms(r['f'])/max(rms(r['out']),1e-9):.2f}")
    print(f"   output sign-changes/sec={osc:4.1f}   desiredCurv rms={rms(r['curv_des']):.5f}")
    # bucket step vs speed
    if len(r['vego']) and len(r['sa_des']) and len(r['t']):
      # interpolate vego onto sa_des timeline
      vt=np.interp(r['t'], r['ts'], r['vego']) if len(r['ts'])>1 else np.full_like(r['t'],vmean)
      d=np.abs(np.diff(r['sa_des'])); vv=vt[:-1]
      all_steps.extend(d); all_step_v.extend(vv); all_osc.append(osc); all_osc_v.append(vmean)
      all_ae.extend(ae_abs); all_ae_v.extend(np.interp(r['t'][:len(ae_abs)], r['ts'], r['vego']) if len(r['ts'])>1 else [vmean]*len(ae_abs))
    print()
  # ---- aggregate by speed bucket ----
  print("\n========== AGGREGATE: steer CMD step/tick (deg) by speed ==========")
  all_steps=np.array(all_steps); all_step_v=np.array(all_step_v)
  print(f"  {'speed(m/s)':<12}{'n':>7}{'mean':>8}{'p50':>8}{'p95':>8}{'max':>8}")
  for (a,b),bn in zip(BUCK,BN):
    m=(all_step_v>=a)&(all_step_v<b)
    if m.sum()<5: continue
    d=all_steps[m]
    print(f"  {bn:<12}{m.sum():>7}{np.mean(d):>8.2f}{np.median(d):>8.2f}{pct(d,95):>8.2f}{np.max(d):>8.2f}")
  print(f"  {'ALL':<12}{len(all_steps):>7}{np.mean(all_steps):>8.2f}{np.median(all_steps):>8.2f}{pct(all_steps,95):>8.2f}{np.max(all_steps):>8.2f}")
  print("\n========== AGGREGATE: |angleError| (deg) by speed ==========")
  all_ae=np.array(all_ae); all_ae_v=np.array(all_ae_v)
  print(f"  {'speed(m/s)':<12}{'n':>7}{'mean|e|':>9}{'p95|e|':>9}")
  for (a,b),bn in zip(BUCK,BN):
    m=(all_ae_v>=a)&(all_ae_v<b)
    if m.sum()<5: continue
    print(f"  {bn:<12}{m.sum():>7}{np.mean(all_ae[m]):>9.2f}{pct(all_ae[m],95):>9.2f}")
