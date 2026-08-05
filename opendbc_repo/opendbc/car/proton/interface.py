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
      ret.steerActuatorDelay = 0.17  # measured ~170ms (cmd vs actual steer angle, full-rate rlog); KA1 had 0.13
      # Lateral controller: torque (self-tuning via locationd/torqued) instead of PID.
      # Seed = torqued-converged values (route 2026-08-05--06-20-13, calPerc 100%): factor ~1.44, offset ~-0.017, friction ~0.136.
      # torqued refines online ('proton' in ALLOWED_CARS); seeding the measured values gives an accurate
      # cold-start after any torqued reset (was originally [2.5, 0.1, 0.0], then [1.5, 0.1, -0.09]).
      ret.lateralTuning.init("torque")
      ret.lateralTuning.torque.latAccelFactor = 1.44
      ret.lateralTuning.torque.friction = 0.14
      ret.lateralTuning.torque.latAccelOffset = -0.02
      ret.lateralTuning.torque.steeringAngleDeadzoneDeg = 0.0
      # Longitudinal: restore release_ka2 gains. x70-ka2/staging had zeroed kp/ki (base default [0.])
      # -> pure feedforward, no feedback correction. release_ka2 X70 used these (proven on this car).
      ret.longitudinalTuning.kpBP = [0.0, 5.0, 20.0]
      ret.longitudinalTuning.kpV  = [0.7, 0.5, 0.4]
      ret.longitudinalTuning.kiBP = [0.0, 5.0, 20.0]
      ret.longitudinalTuning.kiV  = [0.2, 0.15, 0.1]
      ret.longitudinalActuatorDelay = 0.45  # release_ka2 used 0.4-0.5; x70-ka2 base was 0.6
      ret.lateralParams.torqueBP, ret.lateralParams.torqueV = [[0.0], [500]]
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
