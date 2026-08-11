#pragma once

#include <cstddef>
#include <string>

/**
 * Driving model runner using RKNN C++ API.
 * Supports both 2-file (vision + policy) and 3-file (vision + on_policy + off_policy) architectures.
 * All inputs are converted to float16 before inference.
 * Use when USE_RKNN=1 and driving_vision.rknn / driving_policy.rknn exist.
 */
class DrivingRKNNModel {
 public:
  /**
   * 2-file constructor: vision + policy (the original 0.10.3 / WMI v12 layout).
   * @param vision_path Path to driving_vision.rknn
   * @param policy_path Path to driving_policy.rknn
   * @param vision_output Pre-allocated float buffer for vision output
   * @param policy_output Pre-allocated float buffer for policy output
   */
  DrivingRKNNModel(const std::string& vision_path,
                   const std::string& policy_path,
                   float* vision_output,
                   float* policy_output);

  /**
   * 3-file constructor: vision + on_policy + off_policy (OPM10V3 / post-0.11 layout).
   * on_policy and off_policy share identical input contracts (desire_pulse, traffic_convention,
   * features_buffer). The constructor loads all 3 models; run_off_policy() is only callable
   * when constructed with 3 paths.
   * @param vision_path Path to driving_vision_opm10v3.rknn
   * @param on_policy_path Path to driving_on_policy_opm10v3.rknn
   * @param off_policy_path Path to driving_off_policy_opm10v3.rknn
   * @param vision_output Pre-allocated float buffer for vision output
   * @param on_policy_output Pre-allocated float buffer for on_policy output
   * @param off_policy_output Pre-allocated float buffer for off_policy output
   */
  DrivingRKNNModel(const std::string& vision_path,
                   const std::string& on_policy_path,
                   const std::string& off_policy_path,
                   float* vision_output,
                   float* on_policy_output,
                   float* off_policy_output);

  ~DrivingRKNNModel();

  /** Run vision model. img and big_img are uint8 NCHW (1,12,128,256). Converted to float16 (raw 0..255) internally. */
  void run_vision(const unsigned char* img, const unsigned char* big_img);

  /**
   * Run policy model (2-file: the single policy head).
   * Also used for on_policy in the 3-file layout — same input contract.
   * All inputs float32; converted to float16 internally.
   */
  void run_policy(const float* desire_pulse,   // (1, 25, 8) = 200
                 const float* traffic_convention,  // (1, 2) = 2
                 const float* features_buffer);    // (1, 25, 512) = 12800

  /**
   * Run off_policy model (3-file only). Identical input contract to run_policy.
   * Must only be called when constructed with the 3-file constructor.
   * Asserts if the 3rd model was not loaded.
   */
  void run_off_policy(const float* desire_pulse,
                      const float* traffic_convention,
                      const float* features_buffer);

  /** Last vision run duration in microseconds (from rknn_query PERF_RUN). */
  int64_t get_vision_run_us() const { return vision_run_us_; }
  /** Last policy run duration in microseconds. */
  int64_t get_policy_run_us() const { return policy_run_us_; }
  /** Last off_policy run duration in microseconds (0 if 2-file mode). */
  int64_t get_off_policy_run_us() const { return off_policy_run_us_; }

 private:
  struct ModelCtx;
  static void load_model(const std::string& path, ModelCtx* out);
  static void cache_policy_indices(ModelCtx* m);
  static void prealloc_output(ModelCtx* m, float* out_buf);
  /** Shared policy inference body — works for any policy-shaped ModelCtx. */
  void run_policy_ctx(ModelCtx* m, const float* desire_pulse,
                      const float* traffic_convention, const float* features_buffer);

  ModelCtx* vision_ctx_;
  ModelCtx* policy_ctx_;       // 2-file: the policy head. 3-file: on_policy head.
  ModelCtx* off_policy_ctx_;   // 3-file only; nullptr in 2-file mode.
  float* vision_output_;
  float* policy_output_;       // 2-file: policy output. 3-file: on_policy output.
  float* off_policy_output_;   // 3-file only; nullptr in 2-file mode.
  int64_t vision_run_us_;
  int64_t policy_run_us_;
  int64_t off_policy_run_us_;
};
