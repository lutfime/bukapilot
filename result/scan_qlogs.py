#!/usr/bin/env python3
"""Scan downloaded qlogs: per-route engagement, controller type, speed profile.
Helps pick rlog segments worth full-rate PID analysis."""
import sys, glob, os, numpy as np
sys.path.insert(0, "/Users/WanLutfi/Documents/Xcode/Kommu")
from cereal import log as capnp_log
EV = capnp_log.Event
nan = float("nan")
import zstandard as zstd

def parse(path):
  with open(path, "rb") as f: data = f.read()
  try: return list(EV.read_multiple_bytes(data))
  except Exception: pass
  try: return list(EV.read_multiple_bytes(zstd.ZstdDecompressor().decompress(data)))
  except Exception:
    r = zstd.ZstdDecompressor().stream_reader(data); return list(EV.read_multiple_bytes(r.read()))

def safe(o, *p, d=nan):
  cur = o
  for x in p:
    try: cur = getattr(cur, x)
    except Exception: return d
  return cur

def which(o):
  try: return o.which()
  except Exception: return "?"

def scan_route(route_glob):
  segs = sorted(glob.glob(route_glob), key=lambda p: int(os.path.basename(os.path.dirname(p)).split("--")[-1]))
  car_name = "?"; lat_type = "?"; ltype_counts = {}
  engaged_segs = []
  all_v = []
  tot_evt = 0
  for q in segs:
    seg = os.path.basename(os.path.dirname(q))
    ev = parse(q)
    tot_evt += len(ev)
    v = []; eng = 0; cs = 0
    seg_lat = {}
    for e in ev:
      if e.which() == "carParams":
        car_name = str(safe(e.carParams, "carName", d=car_name))
        lat_type = str(e.carParams.lateralTuning.which())
      if e.which() == "controlsState":
        cs += 1
        ls = which(e.controlsState.lateralControlState)
        seg_lat[ls] = seg_lat.get(ls,0)+1
        if safe(e.controlsState, "enabled", d=False): eng += 1
        ltype_counts[ls] = ltype_counts.get(ls,0)+1
      if e.which() == "carState":
        vv = safe(e.carState, "vEgo", d=nan)
        if vv == vv: v.append(vv)
    all_v.extend(v)
    eng_frac = eng/cs if cs else 0
    vmean = np.mean(v) if v else 0
    engaged_segs.append((seg, len(ev), cs, eng, eng_frac, vmean, max(seg_lat, key=seg_lat.get) if seg_lat else "-"))
  return dict(route=segs[0].split("/qlog")[0].split("/drives/")[1] if segs else "?",
              nseg=len(segs), car=car_name, lat=lat_type, lat_counts=ltype_counts,
              tot_evt=tot_evt, segs=engaged_segs, v=all_v)

if __name__ == "__main__":
  for rg in ["result/drives/2026-08-08--13-44-09--*/qlog.zst",
             "result/drives/2026-08-08--12-19-52--*/qlog.zst"]:
    if not glob.glob(rg): continue
    r = scan_route(rg)
    v = np.array(r["v"]) if r["v"] else np.array([0])
    print(f"\n=== ROUTE {r['route']} ===")
    print(f"  carName={r['car']}  lateralTuning.which={r['lat']}  segs={r['nseg']}  events={r['tot_evt']}")
    print(f"  latControlState counts: {r['lat_counts']}")
    if len(r["v"]):
      print(f"  vEgo (m/s): mean={np.mean(v):.1f} p10={np.percentile(v,10):.1f} p50={np.percentile(v,50):.1f} p90={np.percentile(v,90):.1f} max={np.max(v):.1f}")
      print(f"  speed buckets (engaged-ish): <5:{np.sum(v<5)}  5-15:{np.sum((v>=5)&(v<15))}  15-25:{np.sum((v>=15)&(v<25))}  >25:{np.sum(v>=25)} samples")
    print(f"  {'seg':<28} {'ev':>5} {'cs':>4} {'eng':>4} {'eng%':>5} {'vEgo':>5} {'latSt':>6}")
    for s in r["segs"]:
      print(f"  {s[0]:<28} {s[1]:>5} {s[2]:>4} {s[3]:>4} {s[4]*100:>4.0f}% {s[5]:>5.1f} {s[6]:>6}")
