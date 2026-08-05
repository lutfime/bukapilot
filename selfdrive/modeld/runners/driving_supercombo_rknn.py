"""
Driving model runner for the fused 0.11 supercombo model (single .rknn).
All inputs are cast to float16 before inference.
Requires: driving_supercombo.rknn + driving_supercombo_metadata.pkl in the model folder.
"""
from __future__ import annotations

import os
import pickle
from pathlib import Path

import numpy as np

try:
  from rknnlite.api import RKNNLite
except ImportError:
  RKNNLite = None  # type: ignore


def _to_fp16(x: np.ndarray) -> np.ndarray:
  """Cast to float16 without changing value range."""
  return x.astype(np.float16)


class DrivingSupercomboRKNNRunner:
  """Runs the fused 0.11 driving_supercombo model via RKNN.

  Unlike the split vision+policy runner, this loads ONE model and feeds ALL 6 inputs
  (img, big_img, desire_pulse, traffic_convention, action_t, features_buffer) in a single
  inference call. The vision→policy chaining happens inside the model.
  """

  def __init__(self, model_dir: Path):
    self.model_dir = Path(model_dir)
    meta_path = self.model_dir / "driving_supercombo_metadata.pkl"
    rknn_path = self.model_dir / "driving_supercombo.rknn"

    if not rknn_path.exists():
      raise FileNotFoundError(f"RKNN model not found: {rknn_path.name}")
    if RKNNLite is None:
      raise ImportError("rknnlite is required (pip install rknn-toolkit2-lite)")

    with open(meta_path, "rb") as f:
      meta = pickle.load(f)

    self.input_shapes = meta["input_shapes"]
    self.output_slices = meta["output_slices"]
    self.output_shape = meta["output_shapes"]["outputs"]
    self.input_names = list(self.input_shapes.keys())
    # e.g. ['img', 'big_img', 'desire_pulse', 'traffic_convention', 'action_t', 'features_buffer']
    self.output_size = int(np.prod(self.output_shape))

    # The features_buffer shape the model expects (0.11 = (1,24,512), 0.10 was (1,25,512))
    self._fb_shape = self.input_shapes["features_buffer"]

    # Load RKNN — pin to NPU core 2 by default (same as the split runner).
    # The split runner pins driving to core 2 and dmonitoring to cores 0+1 to avoid contention.
    core_mask = os.getenv("RKNN_DRIVING_CORE_MASK", "0x4")
    core_map = {
      "0x4": getattr(RKNNLite, "NPU_CORE_2", None),
      "2":   getattr(RKNNLite, "NPU_CORE_2", None),
      "0":   getattr(RKNNLite, "NPU_CORE_0", None),
      "1":   getattr(RKNNLite, "NPU_CORE_1", None),
      "0_1_2": getattr(RKNNLite, "NPU_CORE_0_1_2", None),
    }
    self._rknn = RKNNLite(verbose=False)
    self._rknn.load_rknn(str(rknn_path))
    self._rknn.init_runtime(core_mask=core_map.get(core_mask))

    # Vision layout options (carry over from the split runner for consistency)
    vision_fmt = os.getenv("RKNN_PY_VISION_FORMAT", "nchw").lower()
    vision_layout = os.getenv("RKNN_PY_VISION_LAYOUT", "nhwc").lower()
    if vision_layout not in ("nchw", "nhwc"):
      vision_layout = "nhwc"
    self._vision_enforce_nchw = os.getenv("RKNN_ENFORCE_VISION_NCHW", "0") != "0"
    if self._vision_enforce_nchw:
      vision_layout = "nchw"
    self._vision_layout = vision_layout
    self._nhwc_bigimg_affine_enable = os.getenv("RKNN_NHWC_BIGIMG_AFFINE_ENABLE", "1") != "0"
    self._nhwc_bigimg_scale = float(os.getenv("RKNN_NHWC_BIGIMG_SCALE", "0.55"))
    self._nhwc_bigimg_bias = float(os.getenv("RKNN_NHWC_BIGIMG_BIAS", "-6.0"))

  def run(self, img: np.ndarray, big_img: np.ndarray,
          desire_pulse: np.ndarray, traffic_convention: np.ndarray,
          action_t: np.ndarray, features_buffer: np.ndarray) -> np.ndarray:
    """Run the fused supercombo model. All inputs cast to float16. Returns float32 flat output."""
    # --- prepare image inputs (cast uint8 → float16, same as split runner) ---
    img_fp16 = np.ascontiguousarray(_to_fp16(img.reshape(self.input_shapes["img"])))
    big_img_arr = big_img.reshape(self.input_shapes["big_img"])
    if self._vision_layout == "nhwc" and self._nhwc_bigimg_affine_enable:
      big_img_arr = np.clip(
        big_img_arr.astype(np.float32) * self._nhwc_bigimg_scale + self._nhwc_bigimg_bias,
        0.0, 255.0
      ).astype(np.uint8)
    big_img_fp16 = np.ascontiguousarray(_to_fp16(big_img_arr))
    if self._vision_layout == "nhwc":
      img_fp16 = np.ascontiguousarray(np.transpose(img_fp16, (0, 2, 3, 1)))
      big_img_fp16 = np.ascontiguousarray(np.transpose(big_img_fp16, (0, 2, 3, 1)))

    # --- prepare vector inputs (cast to float16) ---
    dp = np.ascontiguousarray(_to_fp16(desire_pulse.reshape(self.input_shapes["desire_pulse"])))
    tc = np.ascontiguousarray(_to_fp16(traffic_convention.reshape(self.input_shapes["traffic_convention"])))
    at = np.ascontiguousarray(_to_fp16(action_t.reshape(self.input_shapes["action_t"])))
    # features_buffer: truncate to model's expected shape if queue produced more frames
    fb = features_buffer.reshape(-1)
    fb_needed = int(np.prod(self._fb_shape))
    if fb.size > fb_needed:
      fb = fb[:fb_needed]
    fb = np.ascontiguousarray(_to_fp16(fb.reshape(self._fb_shape)))

    # Feed inputs in the order the model expects (matching input_shapes key order)
    feed_map = {
      "img": img_fp16,
      "big_img": big_img_fp16,
      "desire_pulse": dp,
      "traffic_convention": tc,
      "action_t": at,
      "features_buffer": fb,
    }
    inputs = [feed_map[name] for name in self.input_names]

    outputs = self._rknn.inference(inputs=inputs, data_type="float16")
    if outputs is None:
      raise RuntimeError("RKNNLite supercombo inference returned None")
    assert len(outputs) == 1
    out = outputs[0]
    if out.dtype != np.float32:
      out = out.astype(np.float32)
    return out.reshape(self.output_shape)
