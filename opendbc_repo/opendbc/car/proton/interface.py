#!/usr/bin/env python3
from cereal import car
from opendbc.car import Bus, get_safety_config
from opendbc.car.interfaces import CarInterfaceBase
from opendbc.car.proton.carcontroller import CarController
from opendbc.car.proton.carstate import CarState
from opendbc.car.proton.radar_interface import RadarInterface
from opendbc.car.proton.values import CAR, DBC, ProtonSafetyFlags

from openpilot.common.features import Features


class CarInterface(CarInterfaceBase):
  CarState = CarState
  CarController = CarController
  RadarInterface = RadarInterface

  @staticmethod
  def _get_params(ret, candidate, fingerprint, car_fw, alpha_long, is_release, docs):
    ret.brand = "proton"

    # No radar DBC = no tracks; X50 waits for bus1 (0x20) wake before first publish.
    ret.radarUnavailable = Bus.radar not in DBC[candidate]

    ret.safetyConfigs = [get_safety_config(car.CarParams.SafetyModel.proton)]
    safety_param = ProtonSafetyFlags(0)
    if Features().has("stock-acc"):
      safety_param |= ProtonSafetyFlags.STOCK_ACC
    if candidate == CAR.PROTON_X50:
      safety_param |= ProtonSafetyFlags.IGNORE_IGNITION_LINE
    ret.safetyConfigs[0].safetyParam = int(safety_param)

    ret.steerControlType = car.CarParams.SteerControlType.torque
    ret.steerLimitTimer = 0.1
    ret.steerActuatorDelay = 0.30          # default; X70 overrides below

    ret.lateralTuning.init("pid")

    ret.lateralTuning.pid.kpBP = [0.0, 5.0, 25.0, 35.0, 40.0]
    ret.lateralTuning.pid.kpV = [0.05, 0.05, 0.15, 0.15, 0.16]
    ret.lateralTuning.pid.kiBP = [0.0, 5.0, 20.0, 30.0]
    ret.lateralTuning.pid.kiV = [0.05, 0.10, 0.20, 0.40]
    ret.lateralTuning.pid.kf = 0.00007

    ret.centerToFront = ret.wheelbase * 0.44
    ret.tireStiffnessFactor = 0.7933
    ret.longitudinalActuatorDelay = 0.6

    ret.openpilotLongitudinalControl = True
    ret.wheelSpeedFactor = 1.02

    if candidate == CAR.PROTON_X50:
      ret.lateralParams.torqueBP, ret.lateralParams.torqueV = [[0.0], [545]]
    elif candidate == CAR.PROTON_S70:
      ret.lateralParams.torqueBP, ret.lateralParams.torqueV = [[0.0], [530]]
    elif candidate == CAR.PROTON_X70:
      # --- Shared X70 car properties (vehicle-model params, used by BOTH lateral controllers) ---
      ret.steerRatio = 20.0             # MEASURED ~20-21 (CAN steer angle / IMU curvature / wheelbase); CarSpecs 15.0 & KA1 16.0 were ~30% low
      ret.steerActuatorDelay = 0.17     # measured ~170ms (cmd vs actual steer angle, full-rate rlog); KA1 had 0.13
      ret.tireStiffnessFactor = 0.9871  # KA1-measured (0.10's 0.7933 is a wrong Camry copy-paste)
      ret.lateralParams.torqueBP, ret.lateralParams.torqueV = [[0.0], [580]]  # STEER_MAX — ceiling is 598 (DBC 11-bit STEER_CMD) / 599 (panda PROTON_MAX_STEER_SEEN); 580 keeps 18-unit headroom
      # Longitudinal: restore release_ka2 gains (x70-ka2/staging had zeroed kp/ki). Shared across lateral mode.
      ret.longitudinalTuning.kpBP = [0.0, 5.0, 20.0]
      ret.longitudinalTuning.kpV  = [0.7, 0.5, 0.4]
      ret.longitudinalTuning.kiBP = [0.0, 5.0, 20.0]
      ret.longitudinalTuning.kiV  = [0.2, 0.15, 0.1]
      ret.longitudinalActuatorDelay = 0.45  # release_ka2 used 0.4-0.5; x70-ka2 base was 0.6
      # --- Lateral controller toggle (KommuDrive app). DEFAULT = PID (KA1-tuned: smooth, no
      # low-speed railing). Torque is opt-in (toggle file = "0"). Stored as a raw file in /data/params/
      # (NOT /data/params/d/ — clearAll deletes unregistered files in d/ on every boot). KommuDrive
      # writes /data/params/X70UsePidController = "1" (PID, default) or "0" (torque).
      try:
        with open("/data/params/X70UsePidController") as _f:
          _use_pid = _f.read().strip() != "0"
      except (FileNotFoundError, OSError):
        _use_pid = True
      if _use_pid:
        # PID: KA1-tuned (ports directly to 0.10 — same +/-1.0 output scale, ff=kf*angle*vEgo^2, angle error).
        # Low kpV[0]=0.0005 = the smooth, no-wobble/no-reengage-slam feel KA1 had (vs torque's huge low-speed kp).
        ret.lateralTuning.init("pid")
        ret.lateralTuning.pid.kpBP = [0.0, 5.0, 15.0, 25.0, 35.0]
        ret.lateralTuning.pid.kpV  = [0.0005, 0.02, 0.045, 0.10, 0.17]  # cut ~25-29% at 54/90 km/h to damp corner overshoot (comma #599: Kp x laggy feedback = oscillation). 0/18/126 km/h unchanged (no data). ITERATE: verify overshoot shrinks on next drive.
        ret.lateralTuning.pid.kiBP = [0.0, 5.0, 15.0, 25.0, 35.0]
        ret.lateralTuning.pid.kiV  = [0.001, 0.01, 0.09, 0.4, 0.5]
        ret.lateralTuning.pid.kf   = 0.000006
      else:
        # Torque (self-tuning via locationd/torqued) — OPT-IN (toggle off). Seed = torqued-converged; torqued refines online. Stock KP_INTERP rails at low speed on this car -> wobble (see result/lateral_analysis.py).
        ret.lateralTuning.init("torque")
        ret.lateralTuning.torque.latAccelFactor = 1.44
        ret.lateralTuning.torque.friction = 0.14
        ret.lateralTuning.torque.latAccelOffset = -0.02
        ret.lateralTuning.torque.steeringAngleDeadzoneDeg = 0.0
    elif candidate == CAR.PROTON_X90:
      ret.lateralParams.torqueBP, ret.lateralParams.torqueV = [[0.0], [256]]
      ret.lateralTuning.pid.kiV = [0.05, 0.05, 0.05, 0.05]
    else:
      ret.dashcamOnly = True
      ret.safetyModel = car.CarParams.SafetyModel.noOutput

    ret.stopAccel = -0.8
    ret.startingState = True
    ret.startAccel = 1.2
    ret.minEnableSpeed = -1
    ret.enableBsm = True
    ret.stoppingDecelRate = 0.3

    return ret

  def _update(self, c):
    ret = self.CS.update(self.cp, self.cp_cam)
    events = self.create_common_events(ret)
    ret.events = events.to_msg()
    return ret

  def apply(self, c, now_nanos):
    return self.CC.update(c, self.CS, now_nanos)
