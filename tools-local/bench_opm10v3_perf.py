#!/usr/bin/env python3
"""Benchmark opm10v3 inference speed: all-Python vs C++ hybrid, per-op + full frame.

CRITICAL: runs in PERFORMANCE mode (8 cores, HARDWARE.set_power_save(False)) because offroad
power-save (5 cores) under-reports Hz by ~2x. The NPU is fixed at 1 GHz regardless; only the CPU
big cores (used for the uint8->fp16 transform + buffer copies) differ. See
OPM10V3-CPP-3SPLIT-FINDINGS.md "Performance" + "GOTCHA" sections.

Run on device (KA2):
  cd /data/openpilot && /usr/local/venv/bin/python tools-local/bench_opm10v3_perf.py
"""
from __future__ import annotations
import sys, time
from pathlib import Path
import numpy as np

def main():
  sys.path.insert(0, "/data/openpilot")
  from openpilot.system.hardware import HARDWARE
  from selfdrive.modeld.runners.driving_3split_rknn import Driving3SplitRKNNRunner
  from selfdrive.modeld.runners.driving_rknnmodel_pyx import DrivingRKNNRunnerCpp
  from rknnlite.api import RKNNLite
  MD = Path("/data/openpilot/selfdrive/modeld/models")

  def cores(): return sum(1 for i in range(8) if open(f"/sys/devices/system/cpu/cpu{i}/online").read().strip()=="1")
  print(f"cores online (power-save): {cores()}")
  HARDWARE.set_power_save(False); time.sleep(0.5)
  print(f"cores online (performance): {cores()}  <- benchmarking in this mode")

  img = np.ascontiguousarray(np.random.RandomState(1).randint(0,256,(1,12,128,256)).astype(np.uint8))
  desire = np.zeros((1,25,8),np.float32); tc = np.array([[1.,0.]],np.float32)
  fb = np.ascontiguousarray(np.zeros((1,25,512),np.float32))
  off_in = [np.ascontiguousarray(desire.astype(np.float16).reshape((1,25,8))),
            np.ascontiguousarray(tc.astype(np.float16)),
            np.ascontiguousarray(fb.astype(np.float16).reshape((1,25,512)))]
  N = 80

  def bench(name, fn):
    for _ in range(15): fn()
    t0 = time.monotonic()
    for _ in range(N): fn()
    ms = (time.monotonic()-t0)/N*1000
    print(f"  {name:34s} {ms:6.2f} ms  ({1000/ms:4.1f} Hz)")

  print("\n--- per-op (isolated) ---")
  cpp = DrivingRKNNRunnerCpp(str(MD/"driving_vision_opm10v3.rknn"), str(MD/"driving_on_policy_opm10v3_erf.rknn"), vision_out_size=632, policy_out_size=1000)
  bench("C++ vision", lambda: cpp.run_vision(img,img))
  bench("C++ on_policy (erf)", lambda: cpp.run_policy(desire,tc,fb))
  del cpp
  for name, mf in [("Python on_policy (erf)","driving_on_policy_opm10v3_erf.rknn"),("Python off_policy","driving_off_policy_opm10v3.rknn")]:
    r = RKNNLite(verbose=False); r.load_rknn(str(MD/mf)); r.init_runtime()
    bench(name, lambda r=r: r.inference(inputs=off_in, data_type="float16"))
    r.release()

  print("\n--- full frame (vision + on_policy + off_policy) ---")
  # all-python
  pr = Driving3SplitRKNNRunner(MD)
  for _ in range(15): pr.run_vision(img,img); pr.run_on_policy(desire,tc,fb); pr.run_off_policy(desire,tc,fb)
  t0=time.monotonic()
  for _ in range(N): pr.run_vision(img,img); pr.run_on_policy(desire,tc,fb); pr.run_off_policy(desire,tc,fb)
  mspy=(time.monotonic()-t0)/N*1000
  print(f"  {'ALL-PYTHON':34s} {mspy:6.2f} ms  ({1000/mspy:4.1f} Hz)")
  for c in [pr._vision_rknn,pr._on_policy_rknn,pr._off_policy_rknn]:
    try: c.release()
    except: pass
  del pr
  # hybrid
  cpp = DrivingRKNNRunnerCpp(str(MD/"driving_vision_opm10v3.rknn"), str(MD/"driving_on_policy_opm10v3_erf.rknn"), vision_out_size=632, policy_out_size=1000)
  offpy = RKNNLite(verbose=False); offpy.load_rknn(str(MD/"driving_off_policy_opm10v3.rknn")); offpy.init_runtime()
  for _ in range(15): cpp.run_vision(img,img); cpp.run_policy(desire,tc,fb); offpy.inference(inputs=off_in,data_type="float16")
  t0=time.monotonic()
  for _ in range(N): cpp.run_vision(img,img); cpp.run_policy(desire,tc,fb); offpy.inference(inputs=off_in,data_type="float16")
  msyb=(time.monotonic()-t0)/N*1000
  print(f"  {'HYBRID C++/Python':34s} {msyb:6.2f} ms  ({1000/msyb:4.1f} Hz)")
  print(f"  -> C++ saves {mspy-msyb:.1f} ms/frame ({1000/msyb - 1000/mspy:.1f} Hz)")
  offpy.release(); del cpp

  HARDWARE.set_power_save(True)
  print("\nrestored power-save")

if __name__ == "__main__":
  import numpy as np  # noqa
  main()
