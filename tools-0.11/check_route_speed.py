"""Parse a qlog/rlog and report vEgo stats. Checks if car was actually driving."""
import sys, os, capnp, io, zstandard

capnp.remove_import_hook()
SCHEMA = "cereal/log.capnp"
log_capnp = capnp.load(SCHEMA)

def read_speeds(path):
    with open(path, "rb") as f:
        dat = f.read()
    # decompress
    if dat.startswith(b'\x28\xb5\x2f\xfd'):
        dctx = zstandard.ZstdDecompressor()
        dat = dctx.decompress(dat, max_output_size=len(dat)*20)
    elif dat.startswith(b'BZh9'):
        import bz2
        dat = bz2.decompress(dat)
    
    speeds = []
    ents = log_capnp.Event.read_multiple_bytes(dat)
    for e in ents:
        try:
            if e.which() == 'carState':
                cs = e.carState
                ve = cs.vEgo
                if hasattr(ve, '__len__'):
                    if len(ve) > 0:
                        speeds.append(float(ve[0]))
                else:
                    speeds.append(float(ve))
        except Exception:
            pass
    return speeds

for path in sys.argv[1:]:
    speeds = read_speeds(path)
    if speeds:
        import numpy as np
        s = np.array(speeds)
        moving = int((s > 1.0).sum())
        pct = 100*moving/len(s)
        print(f"{os.path.basename(path)}:")
        print(f"  {len(s)} samples | vEgo min={s.min():.1f} max={s.max():.1f} mean={s.mean():.1f} m/s")
        print(f"  moving(>1m/s): {moving}/{len(s)} ({pct:.0f}%)", end="")
        if s.max() < 1.0:
            print(" -> PARKED")
        elif pct > 50:
            print(" -> DRIVING")
        else:
            print(" -> MIXED")
    else:
        print(f"{os.path.basename(path)}: no speed data")
