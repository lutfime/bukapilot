#!/usr/bin/env python3
"""Kommu X70 onroad-fault diagnostic. Run on the device:
   /usr/local/venv/bin/python /data/grab_diag.py
Prints: selected model, modeld process status, modeld startup/selection/errors,
and the latest drive's modelV2 count + alerts. Paste output back."""
import os, glob, json, datetime, subprocess

def lg(*a): print(*a)

lg("="*60); lg("KOMMU X70 DIAGNOSTIC"); lg("="*60)

# 1. Selected model + toggles
lg("\n--- model selection ---")
for f in ["/data/params/SelectedDrivingModel", "/data/params/UseSupercomboModel"]:
    try: lg(f"{os.path.basename(f)} = {open(f).read().strip()!r}")
    except: lg(f"{os.path.basename(f)} = (none)")

# 2. modeld / controlsd alive?
lg("\n--- processes (onroad only) ---")
for p in ["modeld", "controlsd", "plannerd", "appbridged"]:
    r = subprocess.run(["pgrep","-af",p], capture_output=True, text=True)
    lines=[l for l in r.stdout.splitlines() if "grab_diag" not in l and "pgrep" not in l]
    lg(f"{p:12s}: {'RUNNING' if lines else 'NOT RUNNING'}")

# 3. swaglog: modeld selection + startup + errors (scan newest 6 files)
lg("\n--- modeld logs (newest swaglog files) ---")
files = sorted(glob.glob("/data/log/swaglog.*"), key=lambda x: os.path.getmtime(x))[-6:]
sel=[]; starts=[]; errs=[]; kills=[]
for F in files:
    try:
        for line in open(F):
            try: d=json.loads(line)
            except: continue
            msg=str(d.get("msg$s","") or d.get("msg","") or "")
            exc=str(d.get("exc_info","") or "")
            fn=d.get("filename","")
            ts=datetime.datetime.fromtimestamp(d.get("created",0)).strftime("%m-%d %H:%M:%S")
            if fn=="modeld.py" and ("selection" in msg or "using RKNN" in msg or "models loaded" in msg or "fall" in msg or "modeld init" in msg):
                starts.append(f"{ts} {msg[:150]}")
            if fn=="modeld.py" and (d.get("level") in ("ERROR","CRITICAL") or "Traceback" in exc):
                errs.append(f"{ts} [{d.get('level')}] {msg[:120]} || EXC: {exc.replace(chr(10),' | ')[:300]}")
            if "selection" in msg and fn=="modeld.py":
                sel.append(f"{ts} {msg[:180]}")
            if fn=="process.py" and "modeld" in msg and ("kill" in msg or "signal" in msg):
                kills.append(f"{ts} {msg[:100]}")
    except: pass
lg("-- selection lines (new) --"); [lg(x) for x in sel[-6:]] if sel else lg("(no 'modeld selection' line yet — modeld hasn't run since the fix)")
lg("-- modeld startup --"); [lg(x) for x in starts[-8:]]
lg("-- modeld errors --"); [lg(x) for x in errs[-8:]] if errs else lg("(none)")
lg("-- modeld killed (graceful vs crash) --"); [lg(x) for x in kills[-6:]] if kills else lg("(none)")

# 4. latest drive: modelV2 count + alerts
lg("\n--- latest drive ---")
try:
    import zstandard as zstd
    from cereal import log as L
    EV=L.Event
    from collections import Counter
    routes = sorted(glob.glob("/data/media/0/realdata/2026-*--*"), key=os.path.getmtime)
    if not routes:
        lg("(no routes found)")
    else:
        route=routes[-1]
        segs=sorted(glob.glob(route+"--*"), key=lambda x:int(x.rsplit("--",1)[1]) if x.rsplit("--",1)[1].isdigit() else -1)
        lg(f"route: {os.path.basename(route)}  ({len(segs)} segments)")
        # try segments from newest back for an intact qlog
        parsed=False
        for seg in reversed(segs):
            q=os.path.join(seg,"qlog.zst")
            if not os.path.exists(q): continue
            try:
                data=open(q,"rb").read()
                evs=list(EV.read_multiple_bytes(zstd.ZstdDecompressor().stream_reader(data).read()))
            except Exception as e:
                continue
            which=Counter(e.which() for e in evs)
            mv=which.get("modelV2",0)
            cs=which.get("controlsState",0)
            lg(f"seg {os.path.basename(seg)}: modelV2={mv} controlsState={cs} carState={which.get('carState',0)}  {'<<< ZERO modelV2 = modeld dead' if mv==0 and cs>0 else ''}")
            # alerts
            csl=[e.controlsState for e in evs if e.which()=="controlsState"]
            an=Counter(c.alertText1 for c in csl if str(getattr(c,'alertText1','')).strip())
            if an: lg(f"  alerts: {dict(list(an.items())[:6])}")
            for e in evs:
                if e.which()=="onroadEvents":
                    ev_names=Counter(str(x.eventType) for x in e.onroadEvents.events)
                    lg(f"  onroadEvents: {dict(ev_names)}")
                    break
            parsed=True
            break
        if not parsed: lg("(could not parse any qlog)")
except Exception as e:
    lg(f"(drive parse failed: {e})")

lg("\n"+"="*60)
