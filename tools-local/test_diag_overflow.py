#!/usr/bin/env python3
"""Run diagnostic RKNN models on DEVICE. Reports which layers output inf.

The diag models have all intermediate layers exposed as outputs (~140/~257).
Run with real features → check each output → the ones with inf are the overflow layers.

Usage on device:
  cd /data/openpilot && /usr/local/venv/bin/python tools-local/test_diag_overflow.py [--frames 50]

Expected result: a short list of layer names that produce inf on the real NPU.
Those are the layers to fix (Clip or weight scaling).
"""
import argparse
import sys
import numpy as np
from pathlib import Path

MODEL_DIR = Path("/data/openpilot/selfdrive/modeld/models")

def run_diag(rknn_file, model_name, n_frames):
    from rknnlite.api import RKNNLite
    
    rknn = RKNNLite(verbose=False)
    rknn.load_rknn(str(MODEL_DIR / rknn_file))
    rknn.init_runtime()
    
    rng = np.random.RandomState(42)
    inf_counts = {}  # output_name -> count of frames with inf/nan
    output_names = None
    
    for frame in range(n_frames):
        dp = np.zeros((1, 25, 8), dtype=np.float16)
        tc = np.array([[1.0, 0.0]], dtype=np.float16)
        base = (rng.randn(512) * 0.3).astype(np.float16)
        fb = np.zeros((1, 25, 512), dtype=np.float16)
        for t in range(25):
            base = np.clip(base + (rng.randn(512) * 0.05).astype(np.float16),
                          np.float16(-1.5), np.float16(1.5))
            fb[0, t] = base
        
        outputs = rknn.inference(inputs=[dp, tc, fb], data_type="float16")
        
        # rknnlite returns outputs in graph order but without names.
        # We map by index — the last output is the final model output.
        if output_names is None:
            print(f"  {model_name}: {len(outputs)} outputs returned")
        
        for i, out in enumerate(outputs):
            if out is None:
                continue
            has_bad = np.isinf(out).any() or np.isnan(out).any()
            if has_bad:
                key = f"output[{i}]"
                inf_counts[key] = inf_counts.get(key, 0) + 1
    
    rknn.release()
    
    print(f"\n  {model_name}: scanned {n_frames} frames")
    if not inf_counts:
        print(f"  NO inf/nan in ANY output (synthetic features may be too tame)")
    else:
        print(f"  Layers with inf/nan ({len(inf_counts)}):")
        for name in sorted(inf_counts.keys()):
            pct = inf_counts[name] / n_frames * 100
            print(f"    {name:40s} {pct:5.1f}% of frames")
    return inf_counts

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--frames", type=int, default=50)
    args = ap.parse_args()
    
    print("=" * 60)
    print("DIAGNOSTIC: find overflow layers on real NPU")
    print("=" * 60)
    
    for rknn_file, name in [
        ("driving_on_policy_opm10v3_diag.rknn", "on_policy"),
        ("driving_off_policy_opm10v3_diag.rknn", "off_policy"),
    ]:
        p = MODEL_DIR / rknn_file
        if p.exists():
            run_diag(rknn_file, name, args.frames)
        else:
            print(f"  SKIP {name}: {rknn_file} not found")
    
    print("\n" + "=" * 60)
    print("Layers listed above = the overflow sources.")
    print("Fix: add Clip to those layers OR scale their weights.")
    print("=" * 60)
