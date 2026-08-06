#!/usr/bin/env python3
"""Open local openpilot rlog drives in PlotJuggler (native, no msgq/visionipc build needed).
usage: pj_open.py <rlog.zt>...   (pass segment files or a glob)
"""
import sys, os, glob, tempfile, subprocess
REPO = "/Users/WanLutfi/Documents/Xcode/Kommu"
sys.path.insert(0, REPO); sys.path.insert(0, os.path.join(REPO, "opendbc_repo"))
from openpilot.tools.lib.logreader import LogReader, save_log
from openpilot.common.basedir import BASEDIR

JDIR = os.path.join(BASEDIR, "tools/plotjuggler"); BIN = os.path.join(JDIR, "bin")
PJ = os.path.join(BIN, "plotjuggler")
assert os.path.exists(PJ), "PlotJuggler not installed in tools/plotjuggler/bin"

files = []
for a in sys.argv[1:]:
  g = sorted(glob.glob(a)); files += g if g else [a]
files = sorted(set(files))
assert files, "no files matched"
print(f"loading {len(files)} segment(s)...")
lr = LogReader(files)
all_data = [d for d in lr if d.which() not in ("can", "sendcan") and not d.which().startswith("customReserved")]
print(f"  {len(all_data)} msgs; writing temp rlog...")
tmp = tempfile.NamedTemporaryFile(suffix=".rlog", dir=JDIR, delete=False).name
save_log(tmp, all_data, compress=False)
del all_data
print(f"launching PlotJuggler on {os.path.basename(tmp)} ...")
env = os.environ.copy(); env["BASEDIR"] = BASEDIR; env["PATH"] = BIN + ":" + env["PATH"]
subprocess.call([PJ, "-d", tmp, "--plugin_folders", BIN], env=env, cwd=JDIR)
os.unlink(tmp)
