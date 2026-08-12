#!/usr/bin/env python3
"""Find modeld crash after the WMI selection in swaglog."""
import json, glob, datetime

wmiv12_t = 0
for F in glob.glob("/data/log/swaglog.*"):
    try:
        for l in open(F):
            if "SelectedDrivingModel': 'wmiv12'" in l:
                try:
                    d = json.loads(l)
                    wmiv12_t = d.get("created", 0)
                except Exception:
                    pass
    except Exception:
        pass

print("WMI selected at:", datetime.datetime.fromtimestamp(wmiv12_t).strftime("%m-%d %H:%M:%S"))

rows = []
for F in glob.glob("/data/log/swaglog.*"):
    try:
        lines = open(F).read().splitlines()
    except Exception:
        continue
    for l in lines:
        try:
            d = json.loads(l)
        except Exception:
            continue
        c = d.get("created", 0)
        if c < wmiv12_t or c > wmiv12_t + 7200:
            continue
        msg = str(d.get("msg$s", "") or d.get("msg", "") or "")
        ex = str(d.get("exc_info", "") or "")
        fn = d.get("filename", "")
        blob = (msg + fn).lower()
        if ("modeld" in blob and ("traceback" in ex or "crash" in msg.lower() or "died" in msg.lower()
              or "error" in msg.lower() or "selection" in msg)) or "modeld selection" in msg:
            rows.append((c, fn, msg[:140], ex.replace("\n", " | ")[:280]))

rows.sort()
print("matching entries:", len(rows))
for c, fn, m, ex in rows[:25]:
    print(datetime.datetime.fromtimestamp(c).strftime("%H:%M:%S"), fn + ":", m)
    if ex:
        print("   EXC:", ex[:280])
