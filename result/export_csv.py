#!/usr/bin/env python3
"""Export an openpilot rlog drive to CSV for PlotJuggler.
Covers lateral PID (angleError/p/i/f/output, desired vs actual angle), curvature,
longitudinal (vEgo/aEgo), and lane/path — everything needed to tune/inspect.
usage: export_csv.py <out.csv> <rlog.zst>...
"""
import sys, csv, numpy as np
sys.path.insert(0, "/Users/WanLutfi/Documents/Xcode/Kommu")
from cereal import log as capnp_log
EV = capnp_log.Event
nan = float("nan")

def parse(path):
  with open(path, "rb") as f: d = f.read()
  try: return list(EV.read_multiple_bytes(d))
  except Exception: pass
  import zstandard as zstd
  try: return list(EV.read_multiple_bytes(zstd.ZstdDecompressor().decompress(d)))
  except Exception:
    r = zstd.ZstdDecompressor().stream_reader(d); return list(EV.read_multiple_bytes(r.read()))
def safe(o, *p, d=nan):
  cur = o
  for x in p:
    try: cur = getattr(cur, x)
    except Exception: return d
  return cur

def main():
  out = sys.argv[1]
  paths = sorted(sys.argv[2:], key=lambda p: int(p.rsplit('--',1)[1].split('.')[0]))
  Tc=[];CMD=[];SOC=[];LAT=[];LONG=[]
  Ts=[];VE=[];AE=[];ANG=[]
  Tl=[];DC=[];CU=[];PID=[]
  Tm=[];LLP=[];PY=[]
  for path in paths:
    try: ev = parse(path)
    except Exception: continue
    for e in ev:
      try: w = e.which()
      except Exception: continue
      t = (e.logMonoTime or 0)/1e9
      if w == "carControl":
        a = e.carControl.actuators
        Tc.append(t); CMD.append(float(safe(a,"torque"))); SOC.append(float(safe(a,"steerOutputCan")))
        LAT.append(bool(safe(e.carControl,"latActive",d=False))); LONG.append(bool(safe(e.carControl,"longActive",d=False)))
      elif w == "carState":
        s = e.carState
        Ts.append(t); VE.append(float(safe(s,"vEgo"))); AE.append(float(safe(s,"aEgo"))); ANG.append(float(safe(s,"steeringAngleDeg")))
      elif w == "controlsState":
        c = e.controlsState; lc = safe(c,"lateralControlState"); pid = safe(lc,"pidState")
        Tl.append(t); DC.append(float(safe(c,"desiredCurvature"))); CU.append(float(safe(c,"curvature")))
        PID.append((float(safe(pid,"steeringAngleDesiredDeg")),float(safe(pid,"angleError")),float(safe(pid,"p")),
                    float(safe(pid,"i")),float(safe(pid,"f")),float(safe(pid,"output")),int(bool(safe(pid,"saturated",d=False))),
                    float(safe(pid,"steeringAngleDeg"))))
      elif w == "modelV2":
        m = e.modelV2
        Tm.append(t); LLP.append([float(x) for x in list(getattr(m,"laneLineProbs",[]))][:4])
        PY.append(list(getattr(safe(m,"position"),"y",[]))[:33])
  Tc = np.array(Tc); t0 = Tc[0]
  VE=np.interp(Tc,Ts,VE); AE=np.interp(Tc,Ts,AE); ANG=np.interp(Tc,Ts,ANG)
  DC=np.interp(Tc,Tl,DC); CU=np.interp(Tc,Tl,CU)
  PIDarr=np.array(PID) if PID else np.zeros((0,8))
  PIDi=np.array([np.interp(Tc,Tl,PIDarr[:,k]) for k in range(8)]).T if len(PIDarr) else np.zeros((len(Tc),8))
  LLParr=np.array([x+[nan]*(4-len(x)) for x in LLP]) if LLP else np.zeros((0,4))
  llp=np.array([np.interp(Tc,Tm,LLParr[:,k]) for k in range(4)]).T if len(LLParr)==len(Tm) else np.zeros((len(Tc),4))
  PYarr=np.array([x+[nan]*(33-len(x)) for x in PY]) if PY else np.zeros((0,33))
  py10=np.interp(Tc,Tm,PYarr[:,8]) if len(PYarr)==len(Tm) else np.zeros(len(Tc))
  py20=np.interp(Tc,Tm,PYarr[:,16]) if len(PYarr)==len(Tm) else np.zeros(len(Tc))

  with open(out, "w", newline="") as f:
    wr = csv.writer(f)
    wr.writerow(["t","vEgo","vEgo_kmh","aEgo","steerCmd","steerOutputCan","steeringAngleDeg",
                 "desiredCurvature","curvature","latacc_actual","latActive","longActive",
                 "pidDesiredAngleDeg","pidAngleError","pid_p","pid_i","pid_f","pid_output","pid_saturated","pidAngleDeg",
                 "laneProb0","laneProb1","laneProb2","laneProb3","pathY_10m","pathY_20m"])
    for i in range(0, len(Tc), 2):  # 50Hz (plenty for ~6Hz wobble)
      wr.writerow([f"{Tc[i]-t0:.3f}",f"{VE[i]:.3f}",f"{VE[i]*3.6:.2f}",f"{AE[i]:.3f}",f"{CMD[i]:.4f}",f"{SOC[i]:.1f}",f"{ANG[i]:.2f}",
                   f"{DC[i]:.6f}",f"{CU[i]:.6f}",f"{CU[i]*VE[i]**2:.3f}",int(LAT[i]),int(LONG[i]),
                   f"{PIDi[i,0]:.2f}",f"{PIDi[i,1]:.3f}",f"{PIDi[i,2]:.4f}",f"{PIDi[i,3]:.4f}",f"{PIDi[i,4]:.5f}",f"{PIDi[i,5]:.4f}",int(PIDi[i,6]),f"{PIDi[i,7]:.2f}",
                   f"{llp[i,0]:.3f}",f"{llp[i,1]:.3f}",f"{llp[i,2]:.3f}",f"{llp[i,3]:.3f}",f"{py10[i]:.3f}",f"{py20[i]:.3f}"])
  print(f"wrote {out}: ~{len(Tc)//2} rows (50Hz), span {Tc[-1]-t0:.0f}s")

if __name__ == "__main__": main()
