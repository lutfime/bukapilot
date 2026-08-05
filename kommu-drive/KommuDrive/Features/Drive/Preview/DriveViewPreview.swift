import SwiftUI

#if DEBUG
/// SwiftUI preview with synthetic data — proves the SceneKit rendering pipeline
/// without a real BLE connection.
struct DriveViewPreview: View {
  @StateObject private var vm: DriveSessionViewModel

  init() {
    let vm = DriveSessionViewModel()
    vm.latestFrame = Self.sampleFrame
    vm.settings = DeviceSettings(
      dongleID: "kommu-e2eed9037197a64e", gitCommit: "abc1234",
      currentVersion: "0.10.3", osVersion: "AGNOS 5", state: "enabled",
      isMetric: true, isOffroad: false, localIP: "192.168.1.42",
      activeWlanSSID: "HomeWiFi", networkType: "Wi-Fi", simStatus: "—",
      enabled: true, quietMode: false, experimentalMode: true,
      alcEnabled: true, ldwEnabled: true, recordFront: false, sshEnabled: false,
      updateAvailable: false, updaterFetchAvailable: false,
      updaterTargetBranch: "x70-test", updaterState: "idle",
      carName: "Proton X70", drivePathOffset: "0", brakeMagGain: nil,
      wifiList: [], availableBranches: "release3-staging,x70-test"
    )
    vm.framesReceived = 256
    vm._debugInjectIndicators(confidence: 0.82, steering: 0.6)
    _vm = StateObject(wrappedValue: vm)
  }

  var body: some View {
    DriveView(viewModel: vm)
  }

  static let sampleFrame = DriveFrame(
    frameId: 1234,
    path: PathData(x: [4, 10, 20, 35, 55, 80],
                   y: [0, 0.2, 0.5, 0.9, 1.4, 2.0]),
    acceleration: [0.1, 0.1, 0.0, -0.1, -0.2, -0.2],
    laneLines: [
      PathData(x: [4, 10, 20, 35, 55, 80], y: [1.8, 1.9, 2.1, 2.4, 2.8, 3.3]),  // l1 (left)
      PathData(x: [4, 10, 20, 35, 55, 80], y: [0.9, 1.0, 1.1, 1.3, 1.6, 2.0]),  // l2
      PathData(x: [4, 10, 20, 35, 55, 80], y: [-0.9, -1.0, -1.1, -1.3, -1.6, -2.0]), // l3
      PathData(x: [4, 10, 20, 35, 55, 80], y: [-1.8, -1.9, -2.1, -2.4, -2.8, -3.3])  // l4 (right)
    ],
    roadEdges: [
      PathData(x: [4, 10, 20, 35, 55, 80], y: [2.8, 3.0, 3.4, 4.0, 4.8, 5.6]),
      PathData(x: [4, 10, 20, 35, 55, 80], y: [-2.8, -3.0, -3.4, -4.0, -4.8, -5.6])
    ],
    leadOne: LeadData(status: 1, distance: 28.0, yRel: 0.1),
    leadTwo: LeadData(status: 0, distance: 0, yRel: 0),
    vEgoCluster: 18.0,      // ~65 km/h
    vCruiseCluster: 22.0,   // ~79 km/h target
    enabled: true,
    experimentalMode: false,
    state: 1,
    alertText1: nil, alertText2: nil, alertStatus: nil,
    personality: 1,
    isMetric: true,
    dongleId: "kommu-test-001",
    confidence: 0.82,
    steeringLimit: 0.6,
    vEgo: 18.0,
    detectedCars: [
      DetectedCar(x: 45, y: -2.5, probability: 0.7, speed: 16),
      DetectedCar(x: 60, y: 3.0, probability: 0.5, speed: 20),
    ],
    laneLineProbs: [0.9, 0.95, 0.85, 0.7],
    roadEdgeStds: [0.1, 0.15]
  )
}

#Preview {
  DriveViewPreview()
    .preferredColorScheme(.dark)
}

#Preview("Connection") {
  ConnectionView(viewModel: DriveSessionViewModel())
    .preferredColorScheme(.dark)
}
#endif
