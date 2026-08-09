#!/usr/bin/env python3
"""Corrected full-rate X70 PID analysis.
Lateral command for Proton = actuators.torque (= PID output p+i+f) * STEER_MAX.
So 'jerk' = |delta output| (normalized). Also regresses output on (angle_des*vEgo^2)
to estimate the effective feedforward gain the P+I are currently synthesizing -> kf target."""
import sys, glob, os, numpy as np
sys.path.insert(0, "/Users/WanLutfi/Documents/Xcode/Kommu")
from cereal import log as capnp_log
import zstandard as zstd
EV = capnp_log.Event
def parse(p):
  data=open(p,"rb").read()
  try: return list(EV.read_multiple_bytes(data))
  except: pass
  r=zstd.ZstdDecompressor().stream_reader(data); return list(EV.read_multiple_bytes(r.read()))
def G(o,*p):
  c=o
  for x in p:
    try: c=getattr(c,x)
    except: return None
  return c
def pct(x,q):
  x=np.asarray(x)[np.isfinite(np.asarray(x))]; return np.percentile(x,q) if len(x) else float('nan')
BUCK=[(0,5),(5,10),(10,15),(15,20),(20,25),(25,35)]; BN=[f"{a}-{b}" for a,b in BUCK]
def rms(x):
  x=np.asarray(x); return float(np.sqrt(np.mean(x**2))) if len(x) else 0.0
def bidx(v):
  for i,(a,b) in enumerate(BUCK):
    if a<=v<b: return i
  return None

def analyze(rlog):
  ev=parse(rlog)
  t=[];out=[];p=[];i=[];f=[];ae=[];ades=[];v=[]
  ts=[];aact=[];srate=[];aego=[]
  tc=[];caccel=[];lcs=[]
  for e in ev:
    w=e.which()
    if w=="controlsState":
      ps=G(e.controlsState,"lateralControlState","pidState")
      if ps is not None:
        t.append(e.logMonoTime)
        out.append(G(ps,"output") or 0.0); p.append(G(ps,"p") or 0.0); i.append(G(ps,"i") or 0.0)
        f.append(G(ps,"f") or 0.0); ae.append(G(ps,"angleError") or 0.0)
        ades.append(G(ps,"steeringAngleDesiredDeg") or 0.0)
    elif w=="carState":
      ts.append(e.logMonoTime)
      aact.append(G(e.carState,"steeringAngleDeg") or 0.0)
      srate.append(abs(G(e.carState,"steeringRateDeg") or 0.0))
      vv=G(e.carState,"vEgo");
      v.append(vv or 0.0)
      ae_=G(e.carState,"aEgo"); aego.append(ae_ if ae_ is not None else float('nan'))
    elif w=="carControl":
      tc.append(e.logMonoTime)
      act=G(e.carControl,"actuators")
      ca=G(act,"accel") if act is not None else None
      caccel.append(ca if ca is not None else float('nan'))
  return dict(seg=os.path.basename(os.path.dirname(rlog)),
    t=np.array(t,float),out=np.array(out),p=np.array(p),i=np.array(i),f=np.array(f),ae=np.array(ae),ades=np.array(ades),
    ts=np.array(ts,float),aact=np.array(aact),srate=np.array(srate),v=np.array(v),aego=np.array(aego),
    tc=np.array(tc,float),caccel=np.array(caccel))

def step(vals,t):
  if len(vals)<2: return (0,0,0,0)
  d=np.abs(np.diff(vals)); return np.mean(d),np.median(d),pct(d,95),np.max(d)

if __name__=="__main__":
  pat = sys.argv[1] if len(sys.argv) > 1 else "result/drives/2026-08-08--1*/rlog.zst"
  segs=sorted(glob.glob(pat))
  print(f"Corrected analysis on {len(segs)} segments [{pat}]. Lateral cmd = PID output (p+i+f) * STEER_MAX(580)\n")
  A_out=[];A_outv=[];A_ades=[];A_adesv=[];A_ae=[];A_aev=[]
  A_jerk=[];A_jerkv=[];A_accelerr=[]
  for s in segs:
    r=analyze(s)
    if len(r['t'])<10: continue
    vm=np.mean(r['v']) if len(r['v']) else 0
    # interpolate vEgo onto controlsState timeline
    vt=np.interp(r['t'], r['ts'], r['v']) if len(r['ts'])>1 else np.full_like(r['t'],vm)
    om,omed,op95,omx=step(r['out'],r['t'])      # OUTPUT steps = the jerk metric
    adm,admed,adp95,admx=step(r['ades'],r['t']) # desired-angle steps = model input noise
    sc=(np.sum(np.diff(np.sign(r['out']))!=0)/((r['t'][-1]-r['t'][0])/1e9)) if len(r['out'])>2 else 0
    print(f"== {r['seg']}  vEgo~{vm:4.1f} m/s ==")
    print(f"   OUTPUT(torque) step/tick: mean={om:.4f} p50={omed:.4f} p95={op95:.4f} max={omx:.4f}  (x580 deg-CAN)")
    print(f"   desiredAngle step/tick:   mean={adm:.3f}° p95={adp95:.3f}° max={admx:.3f}°   (model/kinematic noise)")
    print(f"   rms: p={rms(r['p']):.3f} i={rms(r['i']):.3f} f={rms(r['f']):.3f} OUT={rms(r['out']):.3f} | f/OUT={rms(r['f'])/max(rms(r['out']),1e-9)*100:.1f}%  i/OUT={rms(r['i'])/max(rms(r['out']),1e-9)*100:.0f}%")
    print(f"   angleError: mean|e|={np.mean(np.abs(r['ae'])):.2f}° p95={pct(np.abs(r['ae']),95):.2f}°  out sign-flips/s={sc:.1f}")
    # longitudinal: align cmd_accel (carControl) to aEgo (carState) nearest-time
    long_str=""
    if len(r['caccel']) and len(r['aego']) and len(r['tc']) and len(r['ts']) and len(r['ts'])>2:
      ca=r['caccel']
      ae2=np.interp(r['tc'], r['ts'], np.nan_to_num(r['aego'], nan=0.0))  # aEgo aligned to cmd times
      m=np.isfinite(ca)&np.isfinite(ae2)
      if m.sum()>20:
        err=np.abs(ca[m]-ae2[m])
        dts=np.diff(r['ts'])/1e9; da=np.diff(np.nan_to_num(r['aego'],nan=0.0)); dd=da/dts; dd=dd[(dts>0.01)&(dts<0.5)&np.isfinite(dd)]
        long_str=f"   LONG: |cmd_accel - aEgo| mean={np.mean(err):.2f} p95={pct(err,95):.2f} m/s² | aEgo jerk std={np.std(dd):.2f} m/s³"
        A_accelerr.extend(err.tolist())
      print(long_str)
    # accumulate by speed
    d_out=np.abs(np.diff(r['out'])); vv=vt[:-1]; A_out.extend(d_out); A_outv.extend(vv)
    d_ad=np.abs(np.diff(r['ades'])); A_ades.extend(d_ad); A_adesv.extend(vv)
    A_ae.extend(np.abs(r['ae'])); A_aev.extend(vt[:len(r['ae'])])
    print()

  A_out=np.array(A_out);A_outv=np.array(A_outv);A_ades=np.array(A_ades);A_adesv=np.array(A_adesv)
  A_ae=np.array(A_ae);A_aev=np.array(A_aev)
  print("========== OUTPUT (torque) step/tick by speed — THE JERK METRIC ==========")
  print(f"  {'speed':<10}{'n':>7}{'mean':>8}{'p50':>8}{'p95':>8}{'max':>8}")
  for (a,b),bn in zip(BUCK,BN):
    m=(A_outv>=a)&(A_outv<b); d=A_out[m]
    if m.sum()<5: continue
    print(f"  {bn:<10}{m.sum():>7}{np.mean(d):>8.4f}{np.median(d):>8.4f}{pct(d,95):>8.4f}{np.max(d):>8.4f}")
  print(f"  {'ALL':<10}{len(A_out):>7}{np.mean(A_out):>8.4f}{np.median(A_out):>8.4f}{pct(A_out,95):>8.4f}{np.max(A_out):>8.4f}")
  print("\n========== desired-angle step/tick (deg) by speed — model input noise ==========")
  print(f"  {'speed':<10}{'mean':>8}{'p95':>8}{'max':>8}")
  for (a,b),bn in zip(BUCK,BN):
    m=(A_adesv>=a)&(A_adesv<b); d=A_ades[m]
    if m.sum()<5: continue
    print(f"  {bn:<10}{np.mean(d):>8.3f}{pct(d,95):>8.3f}{np.max(d):>8.3f}")

  # === data-driven kf target: regress OUT on (angle_des * vEgo^2) ===
  print("\n========== FEEDFORWARD (kf) ANALYSIS ==========")
  print("  current kf=6.5e-6; ff term f = kf*angle_des*vEgo^2")
  kf=6.5e-6
  # pool engaged points >=5 m/s (I active), use segments
  X=[];Y=[];F=[]
  for s in segs:
    r=analyze(s);
    if len(r['t'])<10: continue
    vt=np.interp(r['t'], r['ts'], r['v']) if len(r['ts'])>1 else np.full_like(r['t'],0)
    mask=vt>=5
    x=(r['ades'][mask]*vt[mask]**2)  # the ff basis
    X.extend(x); Y.extend(np.abs(r['out'][mask])); F.extend(np.abs(r['f'][mask]))
  X=np.array(X);Y=np.array(Y);F=np.array(F)
  # effective gain the closed loop synthesizes: slope of |out| vs ff-basis (through origin)
  if np.sum(X**2)>0:
    eff=np.sum(X*Y)/np.sum(X**2)   # |out| ~ eff * basis
    print(f"  effective closed-loop gain on (angle_des*vEgo^2): eff={eff:.2e}  vs kf={kf:.2e}")
    print(f"  ratio eff/kf = {eff/kf:.1f}x  -> P+I are synthesizing ~{eff/kf:.0f}x the open-loop feedforward")
    print(f"  Setting kf to ~{eff:.2e} (≈{eff/kf:.0f}x) would let feedforward provide most steady-state torque;")
    print(f"  a conservative first step: kf ~ {3*kf:.2e}-{5*kf:.2e} (3-5x), then iterate.")
