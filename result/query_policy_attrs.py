#!/usr/bin/env python3
"""Query RKNN policy model input/output attrs (on/off/default) to diagnose C++ 3-split inf bug."""
from rknnlite.api import RKNNLite
from pathlib import Path
MD = Path("/data/openpilot/selfdrive/modeld/models")
for name, f in [("on_policy", "driving_on_policy_opm10v3.rknn"),
                ("off_policy", "driving_off_policy_opm10v3.rknn"),
                ("default_policy(2-file,works)", "driving_policy.rknn")]:
    r = RKNNLite(verbose=False)
    r.load_rknn(str(MD / f))
    r.init_runtime()
    print(f"=== {name} ({f}) ===")
    print("  inputs:")
    for i, ia in enumerate(r.get_input_attrs()):
        print(f"    [{i}] name={ia.get('name')!r} shape={ia.get('shape')} dtype={ia.get('dtype')} fmt={ia.get('fmt')} n_elems={ia.get('n_elems')}")
    print("  outputs:")
    for i, oa in enumerate(r.get_output_attrs()):
        print(f"    [{i}] name={oa.get('name')!r} shape={oa.get('shape')} dtype={oa.get('dtype')} n_elems={oa.get('n_elems')} scale={oa.get('scale')} zp={oa.get('zp')} fl={oa.get('fl')}")
    r.release()
    print()
