#!/usr/bin/env python3
"""Find the WMI 'process error': scan recent swaglog for ANY process crash, and parse the
latest drive routes for the processError event / dead process. Run on device."""
import json, glob, os, datetime
from collections import Counter

print("=== 1. ANY process crash/Traceback in newest 30 swaglog files ===")
files = sorted(glob.glob("/data/log/swaglog.*"), key=os.path.getmtime)[-30:]
crashes = []
for F in files:
    try:
        for l in open(F):
            try:
                d = json.loads(l)
            except Exception:
                continue
            ex = str(d.get("exc_info", "") or "")
            msg = str(d.get("msg$s", "") or d.get("msg", "") or "")
            ctx = d.get("ctx", {})
            daemon = ctx.get("daemon", "") if isinstance(ctx, dict) else ""
            if "Traceback" in ex or "crash" in msg.lower() or "died" in msg.lower():
                crashes.append((d.get("created", 0), daemon or d.get("filename", ""), msg[:90], ex.replace("\n", " | ")[:200]))
    except Exception:
        pass
crashes.sort()
print(f"crash entries: {len(crashes)}")
for c, da, m, ex in crashes[-15:]:
    print(datetime.datetime.fromtimestamp(c).strftime("%m-%d %H:%M:%S"), "[" + str(da) + "]", m)
    if "Traceback" in ex:
        print("   EXC:", ex[:200])

print()
print("=== 2. manager 'marking process dead' / not alive (recent) ===")
for F in files:
    try:
        for l in open(F):
            if "not_running" in l or "marking" in l.lower() or "process_dead" in l or "unregistered" in l.lower():
                try:
                    d = json.loads(l)
                    print(datetime.datetime.fromtimestamp(d.get("created", 0)).strftime("%H:%M:%S"),
                          str(d.get("msg$s", "") or d.get("msg", ""))[:120])
                except Exception:
                    pass
    except Exception:
        pass

print()
print("=== 3. latest drive routes (by mtime) ===")
routes = sorted(glob.glob("/data/media/0/realdata/2026-*--*"), key=os.path.getmtime)[-4:]
for r in routes:
    print(" ", os.path.basename(r), datetime.datetime.fromtimestamp(os.path.getmtime(r)).strftime("%m-%d %H:%M"))

# parse the newest route's last segment for modelV2 + events
try:
    import zstandard as zstd
    from cereal import log as L
    EV = L.Event
    if routes:
        segs = sorted(glob.glob(routes[-1] + "--*"),
                      key=lambda x: int(x.rsplit("--", 1)[1]) if x.rsplit("--", 1)[1].isdigit() else -1)
        for seg in reversed(segs[:]):
            q = os.path.join(seg, "qlog.zst")
            if not os.path.exists(q):
                continue
            data = open(q, "rb").read()
            evs = list(EV.read_multiple_bytes(zstd.ZstdDecompressor().stream_reader(data).read()))
            which = Counter(e.which() for e in evs)
            print()
            print("=== 4. newest segment:", os.path.basename(seg), "===")
            print("  modelV2:", which.get("modelV2", 0), " controlsState:", which.get("controlsState", 0))
            for e in evs:
                if e.which() == "onroadEvents":
                    ev_names = Counter(str(x.eventType) for x in e.onroadEvents.events)
                    print("  onroadEvents:", dict(ev_names))
                    break
            # alert text from controlsState
            csl = [e.controlsState for e in evs if e.which() == "controlsState"]
            an = Counter(str(getattr(c, "alertText1DEPRECATED", "")) for c in csl
                         if str(getattr(c, "alertText1DEPRECATED", "")).strip())
            if an:
                print("  alertText1:", dict(list(an.items())[:6]))
            break
except Exception as e:
    print("drive parse failed:", e)
