import os, glob, numpy as np
from PIL import Image

EXTRACTED = 'tools-local/calibration_frames/extracted'
CALIB = 'tools-local/calibration_frames'

segments = {}
for d in sorted(glob.glob(os.path.join(EXTRACTED, '*'))):
    seg = os.path.basename(d)
    base, cam = seg.rsplit('_', 1)
    segments.setdefault(base, {})
    for f in sorted(glob.glob(os.path.join(d, 'frame_*.jpg'))):
        fname = os.path.basename(f)
        segments[base].setdefault(fname, {})[cam] = f

pairs = [(v['fcamera'], v['ecamera']) for v in segments.values() if 'fcamera' in v and 'ecamera' in v]
print(f'Found {len(pairs)} frame pairs')

def transform(path):
    im = Image.open(path).convert("L").resize((256, 128), Image.BILINEAR)
    arr = np.asarray(im, dtype=np.float32)
    return np.tile(arr.reshape(1, 1, 128, 256), (1, 12, 1, 1))

# Vision dataset
VDIR = os.path.join(CALIB, 'calib_vision_npy')
os.makedirs(VDIR, exist_ok=True)
for f in glob.glob(os.path.join(VDIR, '*')): os.remove(f)
lines = []
for i, (fcam, ecam) in enumerate(pairs):
    np.save(os.path.join(VDIR, f's{i:04d}_img.npy'), transform(fcam))
    np.save(os.path.join(VDIR, f's{i:04d}_big.npy'), transform(ecam))
    lines.append(f'calib_vision_npy/s{i:04d}_img.npy calib_vision_npy/s{i:04d}_big.npy')
with open(os.path.join(CALIB, 'dataset_vision.txt'), 'w') as f:
    f.write('\n'.join(lines) + '\n')

# Supercombo dataset
SDIR = os.path.join(CALIB, 'calib_npy')
os.makedirs(SDIR, exist_ok=True)
for f in glob.glob(os.path.join(SDIR, '*')): os.remove(f)
desire = np.zeros((1, 25, 8), dtype=np.float32)
tc = np.array([[1.0, 0.0]], dtype=np.float32)
at = np.zeros((1, 2), dtype=np.float32)
fb = np.zeros((1, 24, 512), dtype=np.float32)
lines = []
for i, (fcam, ecam) in enumerate(pairs):
    for name, arr in [('img', transform(fcam)), ('big_img', transform(ecam)),
                       ('desire_pulse', desire), ('traffic_convention', tc),
                       ('action_t', at), ('features_buffer', fb)]:
        np.save(os.path.join(SDIR, f's{i:04d}_{name}.npy'), arr)
    lines.append(f"calib_npy/s{i:04d}_img.npy calib_npy/s{i:04d}_big_img.npy calib_npy/s{i:04d}_desire_pulse.npy calib_npy/s{i:04d}_traffic_convention.npy calib_npy/s{i:04d}_action_t.npy calib_npy/s{i:04d}_features_buffer.npy")
with open(os.path.join(CALIB, 'dataset.txt'), 'w') as f:
    f.write('\n'.join(lines) + '\n')

print(f'Vision: {len(pairs)} samples')
print(f'Supercombo: {len(pairs)} samples')
