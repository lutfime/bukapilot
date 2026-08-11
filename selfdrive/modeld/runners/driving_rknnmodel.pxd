# Cython declarations for DrivingRKNNModel (C++ RKNN driving runner)
# Supports both 2-file (vision + policy) and 3-file (vision + on_policy + off_policy) architectures.

from libcpp.string cimport string

cdef extern from "selfdrive/modeld/runners/driving_rknnmodel.h":
    cdef cppclass DrivingRKNNModel:
        # 2-file constructor
        DrivingRKNNModel(const string& vision_path,
                        const string& policy_path,
                        float* vision_output,
                        float* policy_output)
        # 3-file constructor
        DrivingRKNNModel(const string& vision_path,
                        const string& on_policy_path,
                        const string& off_policy_path,
                        float* vision_output,
                        float* on_policy_output,
                        float* off_policy_output)
        void run_vision(const unsigned char* img, const unsigned char* big_img)
        void run_policy(const float* desire_pulse,
                        const float* traffic_convention,
                        const float* features_buffer)
        void run_off_policy(const float* desire_pulse,
                            const float* traffic_convention,
                            const float* features_buffer)
        long long get_vision_run_us()
        long long get_policy_run_us()
        long long get_off_policy_run_us()
