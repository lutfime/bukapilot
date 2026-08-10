"""
Driving model runner for the 3-file split (vision + on_policy + off_policy) using RKNN.
This is comma's post-0.11 architecture used by OP Model 10 V3 and similar community models.

Loads 3 RKNNLite instances. Vision runs first; its hidden_state feeds features_buffer for
BOTH policy heads (same pattern as the 2-file split, just with two policy calls instead of one).

Requires: driving_vision_opm10v3.rknn, driving_on_policy_opm10v3.rknn,
          driving_off_policy_opm10v3.rknn + matching metadata pkls in the model folder.
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


class Driving3SplitRKNNRunner:
  """Runs 3 RKNN models: vision encoder + on_policy head + off_policy head.

  Vision runs once per frame. Its hidden_state output feeds the features_buffer
  input of BOTH policy heads (on_policy and off_policy share the same temporal context).
  All inputs are cast to float16 before inference, matching the 2-file split runner.
  """

  def __init__(self, model_dir: Path):
    self.model_dir = Path(model_dir)

    # --- File paths (overridable via env vars) ---
    vision_rknn = self.model_dir / os.getenv("RKNN_3SPLIT_VISION_MODEL", "driving_vision_opm10v3.rknn")
    on_policy_rknn = self.model_dir / os.getenv("RKNN_3SPLIT_ON_POLICY_MODEL", "driving_on_policy_opm10v3.rknn")
    off_policy_rknn = self.model_dir / os.getenv("RKNN_3SPLIT_OFF_POLICY_MODEL", "driving_off_policy_opm10v3.rknn")
    vision_meta = self.model_dir / os.getenv("RKNN_3SPLIT_VISION_META", "driving_vision_opm10v3_metadata.pkl")
    on_policy_meta = self.model_dir / os.getenv("RKNN_3SPLIT_ON_POLICY_META", "driving_on_policy_opm10v3_metadata.pkl")
    off_policy_meta = self.model_dir / os.getenv("RKNN_3SPLIT_OFF_POLICY_META", "driving_off_policy_opm10v3_metadata.pkl")

    for p in (vision_rknn, on_policy_rknn, off_policy_rknn):
      if not p.exists():
        raise FileNotFoundError(f"3-split RKNN model not found: {p.name}")
    if RKNNLite is None:
      raise ImportError("rknnlite is required for 3-split driving runner (pip install rknn-toolkit2-lite)")

    # --- Load metadata ---
    with open(vision_meta, "rb") as f:
      vm = pickle.load(f)
    with open(on_policy_meta, "rb") as f:
      om = pickle.load(f)
    with open(off_policy_meta, "rb") as f:
      fpm = pickle.load(f)

    self.vision_input_shapes = vm["input_shapes"]
    self.vision_output_slices = vm["output_slices"]
    self.vision_output_shape = vm["output_shapes"]["outputs"]
    self.vision_input_names = list(self.vision_input_shapes.keys())  # ['img', 'big_img']
    self.vision_output_size = int(np.prod(self.vision_output_shape))

    # Both policy heads have identical input contracts: desire_pulse, traffic_convention, features_buffer
    self.policy_input_shapes = om["input_shapes"]  # on_policy and off_policy have the same inputs
    self.on_policy_output_slices = om["output_slices"]
    self.on_policy_output_shape = om["output_shapes"]["outputs"]
    self.on_policy_output_size = int(np.prod(self.on_policy_output_shape))

    self.off_policy_output_slices = fpm["output_slices"]
    self.off_policy_output_shape = fpm["output_shapes"]["outputs"]
    self.off_policy_output_size = int(np.prod(self.off_policy_output_shape))

    self.policy_input_names = list(self.policy_input_shapes.keys())

    # --- Load RKNN models ---
    self._vision_rknn = RKNNLite(verbose=False)
    self._vision_rknn.load_rknn(str(vision_rknn))
    self._vision_rknn.init_runtime()

    self._on_policy_rknn = RKNNLite(verbose=False)
    self._on_policy_rknn.load_rknn(str(on_policy_rknn))
    self._on_policy_rknn.init_runtime()

    self._off_policy_rknn = RKNNLite(verbose=False)
    self._off_policy_rknn.load_rknn(str(off_policy_rknn))
    self._off_policy_rknn.init_runtime()

    # --- Vision format config (mirrors DrivingRKNNRunner) ---
    n_vision_in = len(self.vision_input_names)
    vision_pt = int(os.getenv("RKNN_PY_VISION_PT", "0"))
    vision_fmt = os.getenv("RKNN_PY_VISION_FORMAT", "nchw").lower()
    vision_layout = os.getenv("RKNN_PY_VISION_LAYOUT", "nhwc").lower()
    if vision_layout not in ("nchw", "nhwc"):
      vision_layout = "nhwc"
    self._vision_enforce_nchw = os.getenv("RKNN_ENFORCE_VISION_NCHW", "0") != "0"
    if self._vision_enforce_nchw:
      vision_layout = "nchw"
    self._vision_layout = vision_layout
    self._vision_pass_through = [vision_pt] * n_vision_in
    self._vision_data_format = [vision_fmt] * n_vision_in
    self._vision_use_explicit_format = os.getenv("RKNN_PY_VISION_EXPLICIT", "0") != "0"
    self._nhwc_bigimg_affine_enable = os.getenv("RKNN_NHWC_BIGIMG_AFFINE_ENABLE", "1") != "0"
    self._nhwc_bigimg_scale = float(os.getenv("RKNN_NHWC_BIGIMG_SCALE", "0.55"))
    self._nhwc_bigimg_bias = float(os.getenv("RKNN_NHWC_BIGIMG_BIAS", "-6.0"))

    # --- Policy format config (mirrors DrivingRKNNRunner) ---
    n_policy_in = len(self.policy_input_names)
    policy_pt = int(os.getenv("RKNN_PY_POLICY_PT", "0"))
    policy_fmt = os.getenv("RKNN_PY_POLICY_FORMAT", "nchw").lower()
    self._policy_pass_through = [policy_pt] * n_policy_in
    self._policy_data_format = [policy_fmt] * n_policy_in
    self._policy_use_explicit_format = os.getenv("RKNN_PY_POLICY_EXPLICIT", "0") != "0"

  def run_vision(self, img: np.ndarray, big_img: np.ndarray) -> np.ndarray:
    """Run vision model. Identical to DrivingRKNNRunner.run_vision."""
    img_fp16 = np.ascontiguousarray(_to_fp16(img.reshape(self.vision_input_shapes["img"])))
    big_img_arr = big_img.reshape(self.vision_input_shapes["big_img"])
    if self._vision_layout == "nhwc" and self._nhwc_bigimg_affine_enable:
      big_img_arr = np.clip(big_img_arr.astype(np.float32) * self._nhwc_bigimg_scale + self._nhwc_bigimg_bias, 0.0, 255.0).astype(np.uint8)
    big_img_fp16 = np.ascontiguousarray(_to_fp16(big_img_arr))
    if self._vision_layout == "nhwc":
      img_fp16 = np.transpose(img_fp16, (0, 2, 3, 1))
      big_img_fp16 = np.transpose(big_img_fp16, (0, 2, 3, 1))
      img_fp16 = np.ascontiguousarray(img_fp16)
      big_img_fp16 = np.ascontiguousarray(big_img_fp16)
    inputs = [img_fp16, big_img_fp16]
    outputs = None
    if self._vision_use_explicit_format:
      try:
        outputs = self._vision_rknn.inference(
          inputs=inputs, data_type="float16",
          inputs_pass_through=self._vision_pass_through, data_format=self._vision_data_format,
        )
      except Exception:
        self._vision_use_explicit_format = False
      if outputs is None:
        self._vision_use_explicit_format = False
    if outputs is None:
      outputs = self._vision_rknn.inference(inputs=inputs, data_type="float16")
    if outputs is None:
      raise RuntimeError("RKNNLite vision inference returned None")
    assert len(outputs) == 1
    out = outputs[0]
    if out.dtype != np.float32:
      out = out.astype(np.float32)
    return out.reshape(self.vision_output_shape)

  def _run_policy(self, rknn: RKNNLite, desire_pulse: np.ndarray,
                  traffic_convention: np.ndarray, features_buffer: np.ndarray,
                  output_shape: tuple) -> np.ndarray:
    """Shared policy inference for on_policy and off_policy heads.
    Identical input contract: desire_pulse, traffic_convention, features_buffer → float16."""
    dp = _to_fp16(desire_pulse.reshape(self.policy_input_shapes["desire_pulse"]))
    tc = _to_fp16(traffic_convention.reshape(self.policy_input_shapes["traffic_convention"]))
    fb = _to_fp16(features_buffer.reshape(self.policy_input_shapes["features_buffer"]))
    inputs = [np.ascontiguousarray(dp), np.ascontiguousarray(tc), np.ascontiguousarray(fb)]
    outputs = None
    if self._policy_use_explicit_format:
      try:
        outputs = rknn.inference(
          inputs=inputs, data_type="float16",
          inputs_pass_through=self._policy_pass_through, data_format=self._policy_data_format,
        )
      except Exception:
        self._policy_use_explicit_format = False
      if outputs is None:
        self._policy_use_explicit_format = False
    if outputs is None:
      outputs = rknn.inference(inputs=inputs, data_type="float16")
    if outputs is None:
      raise RuntimeError("RKNNLite policy inference returned None")
    assert len(outputs) == 1
    out = outputs[0]
    if out.dtype != np.float32:
      out = out.astype(np.float32)
    return out.reshape(output_shape)

  def run_on_policy(self, desire_pulse: np.ndarray, traffic_convention: np.ndarray,
                    features_buffer: np.ndarray) -> np.ndarray:
    """Run on_policy head → plan + desire_state (driving control outputs)."""
    return self._run_policy(self._on_policy_rknn, desire_pulse, traffic_convention,
                            features_buffer, self.on_policy_output_shape)

  def run_off_policy(self, desire_pulse: np.ndarray, traffic_convention: np.ndarray,
                     features_buffer: np.ndarray) -> np.ndarray:
    """Run off_policy head → plan + lane_lines + lead + road_edges (perception outputs)."""
    return self._run_policy(self._off_policy_rknn, desire_pulse, traffic_convention,
                            features_buffer, self.off_policy_output_shape)
