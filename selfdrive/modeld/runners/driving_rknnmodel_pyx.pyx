# Cython wrapper for DrivingRKNNModel (C++ RKNN driving runner)
# Supports both 2-file (vision + policy) and 3-file (vision + on_policy + off_policy) architectures.
# Use when USE_RKNN=1; inputs are cast to float16 in C++.

# distutils: language = c++
# cython: c_string_encoding=ascii

import numpy as np
cimport numpy as np
from libcpp.string cimport string

from .driving_rknnmodel cimport DrivingRKNNModel

cdef class DrivingRKNNRunnerCpp:
    """Runs driving vision + policy via C++ RKNN. Inputs converted to float16 in C++.

    Two-file mode: vision + policy (0.10.3, WMI v12).
    Three-file mode: vision + on_policy + off_policy (OPM10V3, post-0.11).
    """

    cdef DrivingRKNNModel* _model
    cdef float[:] _vision_out
    cdef float[:] _policy_out       # 2-file: policy. 3-file: on_policy.
    cdef float[:] _off_policy_out   # 3-file only.
    cdef bint _is_3split

    def __cinit__(self, str vision_path, str policy_path,
                  str off_policy_path=None,
                  int vision_out_size=1576, int policy_out_size=1000,
                  int off_policy_out_size=0):
        """Construct the C++ runner.

        2-file: DrivingRKNNRunnerCpp(vision_path, policy_path)
        3-file: DrivingRKNNRunnerCpp(vision_path, on_policy_path, off_policy_path,
                                      vision_out_size=V, policy_out_size=P, off_policy_out_size=F)

        Output buffer sizes MUST match the model's actual output dimensions.
        These are read from the metadata pkls by modeld.py before construction.
        """
        cdef float[::1] v_out = np.zeros(max(vision_out_size, 1), dtype=np.float32)
        cdef float[::1] p_out = np.zeros(max(policy_out_size, 1), dtype=np.float32)
        cdef float[::1] op_out = np.zeros(max(off_policy_out_size, 1), dtype=np.float32)
        self._vision_out = v_out
        self._policy_out = p_out
        self._off_policy_out = op_out
        self._is_3split = off_policy_path is not None

        if off_policy_path is not None:
            # 3-file constructor
            self._model = new DrivingRKNNModel(
                vision_path.encode('utf-8'),
                policy_path.encode('utf-8'),       # on_policy path
                off_policy_path.encode('utf-8'),
                &v_out[0],
                &p_out[0],                         # on_policy output
                &op_out[0],
            )
        else:
            # 2-file constructor (backward compatible)
            self._model = new DrivingRKNNModel(
                vision_path.encode('utf-8'),
                policy_path.encode('utf-8'),
                &v_out[0],
                &p_out[0],
            )

    def __dealloc__(self):
        if self._model is not NULL:
            del self._model
            self._model = NULL

    def run_vision(self, np.ndarray img, np.ndarray big_img):
        """img, big_img: uint8 numpy arrays (1,12,128,256) or flat."""
        cdef np.ndarray[np.uint8_t, ndim=1, mode='c'] img_flat_arr = np.asarray(img, dtype=np.uint8, order='C').reshape(-1)
        cdef np.ndarray[np.uint8_t, ndim=1, mode='c'] big_flat_arr = np.asarray(big_img, dtype=np.uint8, order='C').reshape(-1)
        cdef unsigned char[::1] img_flat = img_flat_arr
        cdef unsigned char[::1] big_flat = big_flat_arr
        self._model.run_vision(&img_flat[0], &big_flat[0])
        return np.asarray(self._vision_out)

    def run_policy(self, np.ndarray desire_pulse, np.ndarray traffic_convention, np.ndarray features_buffer):
        """All float32 numpy arrays. In 3-file mode, this runs on_policy head."""
        cdef np.ndarray[np.float32_t, ndim=1, mode='c'] dp_arr = np.asarray(desire_pulse, dtype=np.float32, order='C').reshape(-1)
        cdef np.ndarray[np.float32_t, ndim=1, mode='c'] tc_arr = np.asarray(traffic_convention, dtype=np.float32, order='C').reshape(-1)
        cdef np.ndarray[np.float32_t, ndim=1, mode='c'] fb_arr = np.asarray(features_buffer, dtype=np.float32, order='C').reshape(-1)
        cdef float[::1] dp = dp_arr
        cdef float[::1] tc = tc_arr
        cdef float[::1] fb = fb_arr
        self._model.run_policy(&dp[0], &tc[0], &fb[0])
        return np.asarray(self._policy_out)

    def run_off_policy(self, np.ndarray desire_pulse, np.ndarray traffic_convention, np.ndarray features_buffer):
        """Run off_policy head (3-file mode only). Same input contract as run_policy."""
        cdef np.ndarray[np.float32_t, ndim=1, mode='c'] dp_arr = np.asarray(desire_pulse, dtype=np.float32, order='C').reshape(-1)
        cdef np.ndarray[np.float32_t, ndim=1, mode='c'] tc_arr = np.asarray(traffic_convention, dtype=np.float32, order='C').reshape(-1)
        cdef np.ndarray[np.float32_t, ndim=1, mode='c'] fb_arr = np.asarray(features_buffer, dtype=np.float32, order='C').reshape(-1)
        cdef float[::1] dp = dp_arr
        cdef float[::1] tc = tc_arr
        cdef float[::1] fb = fb_arr
        self._model.run_off_policy(&dp[0], &tc[0], &fb[0])
        return np.asarray(self._off_policy_out)

    def get_vision_run_us(self):
        return self._model.get_vision_run_us()

    def get_policy_run_us(self):
        return self._model.get_policy_run_us()

    def get_off_policy_run_us(self):
        return self._model.get_off_policy_run_us()

    def is_3split(self):
        return self._is_3split
