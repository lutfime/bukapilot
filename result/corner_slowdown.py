import sys, numpy as np
sys.path.insert(0, "/Users/WanLutfi/Documents/Xcode/Kommu Lutfi")
from cereal import log as capnp_log
EV = capnp_log.Event
def parse(path):
  with open(path,"rb") as f: data=f.read()
  try: return list(EV.read_multiple_bytes(data))
  except Exception: pass
  import zstandard as zstd
  try: return list(EV.read_multiple_bytes(zstd.ZstdDecompressor().decompress(data)))
  except Exception:
    r=zstd.ZstdDecompressor().stream_reader(data); return list(EV.read_multiple_bytes(r.read()))
def safe(o,*p,d=float("nan")):
  cur=o
  for x in p:
    try: cur=getattr(cur,x)
    except Exception: return d
  return cur
SRC={0:"cruise",1:"lead0",2:"lead1",3:"lead2",4:"e2e"}
def main():
  path=sys.argv[1]; ev=parse(path)
  cs_t,ve=[],[]; md_t,desacc,vel0,curv=[],[],[],[]; lp_t,atgt,src=[],[],[]
  rd_t,ddrel,dvrel,dstat=[],[],[],[]; exp=[]
  for e in ev:
    try: w=e.which()
    except Exception: continue
    t=(e.logMonoTime or 0)/1e9
    if w=="carState":
      cs_t.append(t); ve.append(float(safe(e.carState,"vEgo")))
    elif w=="modelV2":
      md_t.append(t)
      desacc.append(float(safe(e.modelV2,"action","desiredAcceleration")))
      vx=safe(e.modelV2,"velocity","x",d=[])
      vel0.append(float(vx[0]) if len(vx) else float("nan"))
      curv.append(float(safe(e.modelV2,"action","desiredCurvature")))
    elif w=="longitudinalPlan":
      lp_t.append(t); atgt.append(float(safe(e.longitudinalPlan,"aTarget")))
      _s = safe(e.longitudinalPlan,"longitudinalPlanSource",d=-1)
      try: src.append(int(_s))
      except Exception:
        try: src.append(int(getattr(_s,"e",-1)))
        except Exception: src.append(-1)
    elif w=="radarState":
      lo=safe(e.radarState,"leadOne")
      rd_t.append(t); ddrel.append(float(safe(lo,"dRel",d=-1))); dvrel.append(float(safe(lo,"vRel",d=0))); dstat.append(int(safe(lo,"status",d=0)))
    elif w=="selfdriveState":
      exp.append(bool(safe(e.selfdriveState,"experimentalMode")))
  cs_t=np.array(cs_t)
  def ip(at,av): return np.interp(cs_t,at,av) if len(at) else np.full(len(cs_t),float("nan"))
  if len(cs_t)<50: print("too few frames"); return
  da=ip(md_t,desacc); v0=ip(md_t,vel0); cv=ip(md_t,curv); at=ip(lp_t,atgt); sr=ip(lp_t,src)
  dr=ip(rd_t,ddrel); vr=ip(rd_t,dvrel); st=ip(rd_t,dstat)
  dt=float(np.median(np.diff(cs_t)))
  print(f"file: {path}  experimental={100*sum(exp)/max(1,len(exp)):.0f}% of frames  vEgo {min(ve)*3.6:.0f}-{max(ve)*3.6:.0f}km/h  dt={dt*1000:.0f}ms")
  print("t(s) vEgo  E2EdesAccel modelVel0 aTarget planSrc lead          curvature")
  last=-1
  for i in range(len(cs_t)):
    s=int(cs_t[i]-cs_t[0])
    if s!=last and s%1==0:  # 1Hz
      last=s
      lead = f"{dr[i]:4.0f}m vRel{vr[i]:+.1f}" if st[i]>0.5 and dr[i]>=0 else "none      "
      sname = SRC.get(int(sr[i]),"?") if (not np.isnan(sr[i]) and sr[i]>=0) else "?"
      print(f"{s:4d} {ve[i]*3.6:5.1f} {da[i]:+8.2f}   {v0[i]*3.6:6.1f}   {at[i]:+6.2f}  {sname:6s} {lead}  {cv[i]:+.2e}")
if __name__=="__main__": main()
