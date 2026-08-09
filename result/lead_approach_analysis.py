#!/usr/bin/env python3
"""Find 'approach a slower lead' events and measure WHEN OP starts to decel.
Key question: does OP see the slow lead early but not brake (tuning), or not see
it until close (detection range)? Reports detection distance vs decel-trigger distance."""
import sys, glob, os, numpy as np; sys.path.insert(0,".")
from cereal import log as capnp_log
import zstandard as zstd
EV=capnp_log.Event
def parse(p):
  data=open(p,"rb").read()
  try: return list(EV.read_multiple_bytes(data))
  except: pass
  r=zstd.ZstdDecompressor().stream_reader(data); return list(EV.read_multiple_bytes(r.read()))
def G(o,*p):
  c=o
  for x in p:
    try:c=getattr(c,x)
    except:return None
  return c

def scan_segs(globpat, label):
  segs=sorted(glob.glob(globpat), key=lambda p:int(os.path.basename(os.path.dirname(p)).split("--")[-1]))
  events=[]   # each: dict with dRel_first, dRel_decel(<=-0.1), dRel_firm(<=-0.6), dRel_min, vEgo, closing, aTarget_before
  for q in segs:
    seg=os.path.basename(os.path.dirname(q))
    ev=parse(q)
    # build time-ordered samples: (t, status, dRel, vRel, vEgo, aTarget, modelProb)
    # NOTE: radarState.vEgo is unpopulated (0) -> use carState.vEgo carried forward
    rows=[]
    at=0.0; vE=0.0
    for e in ev:
      w=e.which()
      if w=="longitudinalPlan": at=G(e.longitudinalPlan,"aTarget") or at
      elif w=="carState": vE=G(e.carState,"vEgo") or vE
      elif w=="radarState":
        l=G(e.radarState,"leadOne")
        if l is not None:
          st=bool(G(l,"status")); d=G(l,"dRel") or 0.0
          vr=G(l,"vRel"); vr=(vr if vr is not None else 0.0)
          mp=G(l,"modelProb") or 0.0
          rows.append((e.logMonoTime, st, d, vr, vE, at, mp))
    # group into approach events: status True, closing < -1.5, vEgo>8
    cur=[]; last_t=0
    def flush(cur):
      if len(cur)<3: return None
      d0=cur[0][2]; v0=cur[0][4]; closing=cur[0][3]; mp0=cur[0][6]
      a_before=np.mean([c[5] for c in cur[:max(1,len(cur)//4)]])  # aTarget early in event
      # first decel crossing
      d_decel=next((c[2] for c in cur if c[5]<=-0.1), None)
      d_firm=next((c[2] for c in cur if c[5]<=-0.6), None)
      d_min=min(c[2] for c in cur)
      ttc_min=min((c[2]/max(-c[3],0.5) for c in cur), default=99)  # d/(vEgo-vLead)=d/(-vRel)
      return dict(seg=seg,n=len(cur),d0=d0,v0=v0,closing=closing,a_before=a_before,mp0=mp0,
                  d_decel=d_decel,d_firm=d_firm,d_min=d_min,ttc_min=ttc_min)
    for r in rows:
      t,st,d,vr,vE,a,mp=r
      ok = st and vr<-1.5 and vE>8
      if ok and (cur==[] or t-last_t<2e9):
        cur.append(r); last_t=t
      else:
        ev2=flush(cur)
        if ev2: events.append(ev2)
        cur=[r] if ok else []; last_t=t
    ev2=flush(cur)
    if ev2: events.append(ev2)
  # report
  print(f"\n===== {label} : {len(events)} approach-to-slower-lead events =====")
  if not events: return
  def col(key):
    return np.array([e[key] for e in events if e[key] is not None], float)
  for key,unit in [("d0","m first-seen"),("a_before"," aT early"),("d_decel","m start-decel"),("d_firm","m firm-brake"),("d_min","m min gap")]:
    c=col(key)
    if len(c)==0: print(f"  {key:10} ({unit}): n/a"); continue
    print(f"  {key:10} ({unit}): n={len(c)} mean={np.mean(c):5.1f} p50={np.percentile(c,50):5.1f} p10={np.percentile(c,10):5.1f}")
  cv=col("closing"); vv=col("v0"); mp=col("mp0")
  print(f"  closing rate mean={np.mean(cv):.1f} m/s | vEgo mean={np.mean(vv):.1f} m/s | lead certainty at first detect mean={np.mean(mp):.2f}")
  print("  sample events (seg, vEgo, closing, dRel first->decel->firm->min):")
  for e in sorted(events,key=lambda x:-x['closing'])[:8]:
    print(f"    {e['seg'][-9:]} v={e['v0']:4.1f} close={e['closing']:5.1f} d0={e['d0']:5.0f} mp0={e['mp0']:.2f} -> decel={str(e['d_decel']):>5} -> firm={str(e['d_firm']):>5} -> min={e['d_min']:4.0f} (aT_early={e['a_before']:+.2f})")

scan_segs("result/drives/2026-08-08--13-44-09--*/qlog.zst", "13-44-09 (qlog)")
scan_segs("result/drives/2026-08-08--12-19-52--*/qlog.zst", "12-19-52 (qlog)")
