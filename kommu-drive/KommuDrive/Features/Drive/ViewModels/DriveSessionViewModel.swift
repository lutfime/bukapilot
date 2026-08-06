import Foundation
import SwiftUI
import Combine

/// `@MainActor` view model that bridges BLE → UI.
///
/// Owns the `BLEManager`, decodes every reassembled message into either a
/// `DriveFrame` (channel 0x01) or `DeviceSettings` (channel 0x02), and exposes
/// the latest of each to SwiftUI.
///
/// Phase 1 also tracks a few derived counters (frame rate, frames received) so
/// the connection screen can prove data is flowing.
@MainActor
final class DriveSessionViewModel: ObservableObject {

  // MARK: Published state for the UI

  /// Most recent decoded driving frame. nil until the first one arrives.
  /// Setter is exposed (rather than `private(set)`) so DEBUG previews can inject
  /// synthetic frames without a BLE connection. Production code only mutates
  /// this via `handle(channel:data:)`.
  @Published var latestFrame: DriveFrame?

  /// Most recent device settings snapshot (~3 Hz).
  @Published var settings: DeviceSettings = .empty

  /// Frames-per-second computed from incoming frame timestamps.
  @Published private(set) var fps: Double = 0

  /// Total frames received this session.
  @Published var framesReceived: Int = 0

  /// Smoothed confidence-ball value in [0, 1] (or -0.5 when disengaged).
  /// Mirrors confidence_ball.py's FirstOrderFilter (cutoff 0.5s at MESSAGE_HZ).
  @Published private(set) var smoothedConfidence: Double = -0.5

  /// Smoothed steering-limit fraction in [-1, 1] for the arc indicator.
  /// Mirrors torque_bar.py's FirstOrderFilter (cutoff 0.1s).
  @Published private(set) var smoothedSteeringLimit: Double = 0

  /// Passthrough of BLE connection state for the connection screen.
  @Published private(set) var connectionState: BLEConnectionState = .disconnected

  /// Pending model/PID toggle values that haven't been sent to the device yet.
  /// nil = no pending change (use device value). Stored here so they survive
  /// SettingsSheet open/close cycles.
  @Published var pendingPidToggle: Bool? = nil
  @Published var pendingModelToggle: Bool? = nil

  // Republished BLE discovery state — SwiftUI views observe the ViewModel, so
  // these mirrors are needed for the ConnectionView list to update in real time.
  @Published private(set) var discoveredPeripherals: [BLEManager.DiscoveredPeripheral] = []
  @Published private(set) var otherPeripherals: [BLEManager.DiscoveredPeripheral] = []
  @Published var showAllDevices: Bool = false
  @Published private(set) var autoReconnectEnabled: Bool = false
  @Published private(set) var reconnectTimedOut: Bool = false

  // MARK: Internals

  let ble = BLEManager()
  private var cancellables = Set<AnyCancellable>()
  private var frameTimestamps: [TimeInterval] = []

  // Filters match the device UI cutoffs. dt = 1/MESSAGE_HZ (16 Hz on device).
  private var confidenceFilter = FirstOrderFilter(initial: -0.5, cutoff: 0.5, dt: 1.0 / 16.0)
  private var steeringFilter   = FirstOrderFilter(initial: 0,    cutoff: 0.1, dt: 1.0 / 16.0)

  #if DEBUG
  /// Inject smoothed indicator values for SwiftUI previews without a BLE feed.
  func _debugInjectIndicators(confidence: Double, steering: Double) {
    smoothedConfidence = confidence
    smoothedSteeringLimit = steering
  }
  #endif

  init() {
    // Bind BLE connection state into our published property.
    ble.$connectionState
      .receive(on: DispatchQueue.main)
      .sink { [weak self] state in self?.connectionState = state }
      .store(in: &cancellables)

    // Republish discovery state so SwiftUI views re-render when devices appear.
    ble.$discoveredPeripherals
      .receive(on: DispatchQueue.main)
      .sink { [weak self] peripherals in self?.discoveredPeripherals = peripherals }
      .store(in: &cancellables)

    ble.$otherPeripherals
      .receive(on: DispatchQueue.main)
      .sink { [weak self] peripherals in self?.otherPeripherals = peripherals }
      .store(in: &cancellables)

    ble.$autoReconnectEnabled
      .receive(on: DispatchQueue.main)
      .sink { [weak self] value in self?.autoReconnectEnabled = value }
      .store(in: &cancellables)

    ble.$reconnectTimedOut
      .receive(on: DispatchQueue.main)
      .sink { [weak self] value in self?.reconnectTimedOut = value }
      .store(in: &cancellables)

    // Two-way bind showAllDevices (view sets it, BLE reads it).
    $showAllDevices
      .receive(on: DispatchQueue.main)
      .sink { [weak self] value in self?.ble.showAllDevices = value }
      .store(in: &cancellables)

    // Handle every complete message that comes back from the device.
    ble.onMessage = { [weak self] channel, data in
      Task { @MainActor in self?.handle(channel: channel, data: data) }
    }

    // Auto-connect to the last known device on launch (no scan needed).
    // Small delay so the central manager has time to power on.
    DispatchQueue.main.asyncAfter(deadline: .now() + 0.5) { [weak self] in
      self?.ble.autoConnectIfKnown()
    }

    #if targetEnvironment(simulator)
    latestFrame = DriveViewPreview.sampleFrame
    _debugInjectIndicators(confidence: 0.82, steering: 0.6)
    framesReceived = 256
    startMockAnimation()
    #endif
  }

  // MARK: App lifecycle

  /// Called when the app returns to the foreground. iOS drops BLE connections
  /// shortly after backgrounding, so we need to reconnect or rescan every time.
  func handleAppBecameActive() {
    if connectionState.isConnected {
      // Still connected (fast background cycle) — re-request visualisation.
      requestVisualisation()
    } else if ble.autoReconnectEnabled {
      // Was connected before, now dropped — try auto-reconnect.
      ble.autoConnectIfKnown()
    } else {
      // No prior connection — start scanning.
      ble.startScan()
    }
  }

  #if targetEnvironment(simulator)
  private var mockTimer: Timer?
  private var mockTime: Double = 0

  private func startMockAnimation() {
    mockTimer?.invalidate()
    mockTimer = Timer.scheduledTimer(withTimeInterval: 0.05, repeats: true) { [weak self] _ in
      Task { @MainActor in
        self?.tickMockAnimation()
      }
    }
  }

  private func tickMockAnimation() {
    mockTime += 0.05
    let t = mockTime

    let leadDist = 28.0 + 6.0 * sin(t * 0.7)
    let speedMps = (68.0 + 7.0 * cos(t * 0.4)) / 3.6
    let steerVal = 0.92 * sin(t * 1.1)
    let confidence = 0.85 + 0.08 * sin(t * 0.5)

    // Build curved winding path
    let steps = 30
    var px: [Double] = []
    var py: [Double] = []
    for i in 0..<steps {
      let x = Double(i) * 3.0
      let curveOffset = sin(t * 0.6 + Double(i) * 0.08) * 2.2
      px.append(x)
      py.append(curveOffset)
    }

    let l1Y = py.map { $0 - 5.5 }
    let l2Y = py.map { $0 - 1.8 }
    let l3Y = py.map { $0 + 1.8 }
    let l4Y = py.map { $0 + 5.5 }

    let mockFrame = DriveFrame(
      frameId: 1000 + Int(t * 20),
      path: PathData(x: px, y: py),
      acceleration: nil,
      laneLines: [PathData(x: px, y: l1Y), PathData(x: px, y: l2Y), PathData(x: px, y: l3Y), PathData(x: px, y: l4Y)],
      roadEdges: [PathData(x: px, y: py.map { $0 - 6.5 }), PathData(x: px, y: py.map { $0 + 6.5 })],
      leadOne: LeadData(status: 1, distance: leadDist, yRel: py.first ?? 0),
      leadTwo: nil,
      vEgoCluster: speedMps,
      vCruiseCluster: 80.0 / 3.6,
      enabled: true,
      experimentalMode: false,
      state: 1,
      alertText1: nil,
      alertText2: nil,
      alertStatus: nil,
      personality: 1,
      isMetric: true,
      dongleId: "kommu-sim-001",
      confidence: confidence,
      steeringLimit: steerVal,
      vEgo: speedMps,
      desiredSpeed: 80.0 / 3.6,
      allowThrottle: true,
      detectedCars: [
        DetectedCar(x: leadDist, y: py.first ?? 0, probability: 0.95, speed: speedMps + 0.8)
      ],
      laneLineProbs: [0.9, 0.9, 0.9, 0.9],
      roadEdgeStds: [0.1, 0.1]
    )

    latestFrame = mockFrame
    framesReceived += 1
    fps = 20.0
    confidenceFilter.update(confidence)
    steeringFilter.update(steerVal)
    smoothedConfidence = confidenceFilter.value
    smoothedSteeringLimit = steeringFilter.value
  }
  #endif

  // MARK: Public actions (called from views)

  func startScanning() { ble.startScan() }
  func stopScanning()  { ble.stopScan() }
  func connect(to id: UUID) { ble.connect(to: id) }
  func disconnect() { ble.disconnect() }

  /// Tell the device which channel the app is currently viewing.
  /// Tell the device to stream visualisation frames (channel 0x01).
  func requestVisualisation() {
    var payload: [String: Any] = ["msgType": "curPage"]
    if let dongle = settings.dongleID {
      payload["deviceList"] = [dongle]
    } else {
      payload["devMode"] = true
    }
    let blob = MsgpackEncoder.encode(payload)
    AppLog.info("→ curPage visualisation (dongle=\(settings.dongleID ?? "nil"), \(blob.count)B)")
    ble.send(blob, channel: BLEProtocol.Channel.visualisation.rawValue)
  }

  /// Tell the device to stream settings frames (channel 0x02).
  func requestSettings() {
    var payload: [String: Any] = ["msgType": "curPage"]
    if let dongle = settings.dongleID {
      payload["deviceList"] = [dongle]
    } else {
      payload["devMode"] = true
    }
    let blob = MsgpackEncoder.encode(payload)
    AppLog.info("→ curPage settings (dongle=\(settings.dongleID ?? "nil"), \(blob.count)B)")
    ble.send(blob, channel: BLEProtocol.Channel.settings.rawValue)
  }

  // MARK: Commands to device (channel 0x02)

  /// Every outbound command must include `deviceList` with our DongleId (or
  /// `devMode: true`) or the device drops it. See `handle_send_channel`.
  /// Commands go on channel 0x02 (settings).
  private func sendCommand(_ fields: [String: Any]) {
    var payload = fields
    if let dongle = settings.dongleID {
      payload["deviceList"] = [dongle]
    } else {
      payload["devMode"] = true
    }
    let blob = MsgpackEncoder.encode(payload)
    ble.send(blob, channel: BLEProtocol.Channel.settings.rawValue)
  }

  /// Save a bool toggle (e.g. OpenpilotEnabledToggle, QuietMode, ...).
  /// Maps to `msgType: 'saveToggle'` in appbridged.
  func saveToggle(_ key: String, value: Bool) {
    sendCommand(["msgType": "saveToggle", key: value])
  }

  /// Reboot the device. Only acts when openpilot is disabled.
  func rebootDevice() {
    sendCommand(["msgType": "reboot"])
  }

  /// Reset calibration (clears CalibrationParams, LiveParameters, etc.).
  func resetCalibration() {
    sendCommand(["msgType": "resetCalibration"])
  }

  /// Check for software updates.
  func checkForUpdate() {
    sendCommand(["msgType": "update", "action": "check"])
  }

  /// Install a fetched update (triggers reboot).
  func installUpdate() {
    sendCommand(["msgType": "update", "action": "install"])
  }

  /// Fetch update data in the background.
  func fetchUpdate() {
    sendCommand(["msgType": "update", "action": "fetch"])
  }

  /// Switch updater target branch and trigger check.
  func changeTargetBranch(_ branch: String) {
    sendCommand(["msgType": "changeTargetBranch", "targetBranch": branch])
  }

  // MARK: WiFi commands

  /// Ask the device to scan for Wi-Fi networks. Results arrive in the next
  /// settings frame(s) as `wifiList: [{ssid, password: bool}]`.
  func scanWifi() {
    sendCommand(["msgType": "scanWifi"])
  }

  /// Connect to a Wi-Fi network.
  func connectWifi(ssid: String, password: String?) {
    var cmd: [String: Any] = ["msgType": "wifi", "action": "connect", "ssid": ssid]
    if let password { cmd["password"] = password }
    sendCommand(cmd)
  }

  /// Forget a saved Wi-Fi network.
  func forgetWifi(ssid: String) {
    sendCommand(["msgType": "wifi", "action": "forget", "ssid": ssid])
  }

  // MARK: Message handling

  private func handle(channel: UInt8, data: Data) {
    guard let channel = BLEProtocol.Channel(rawValue: channel) else {
      AppLog.warn("unknown channel: \(channel)")
      return
    }
    do {
      let dict = try MsgpackDecoder.decodeMap(data)
      AppLog.debug("rx ch=\(channel.rawValue) keys=\(dict.keys.sorted()) \(data.count)B")
      switch channel {
      case .visualisation:
        let frame = DriveFrameDecoder.decode(dict)
        latestFrame = frame
        framesReceived += 1

        // Confidence ball: park at -0.5 when disengaged (matches confidence_ball.py).
        if frame.enabled, let cf = frame.confidence {
          confidenceFilter.update(cf)
        } else {
          confidenceFilter.update(-0.5)
        }
        smoothedConfidence = confidenceFilter.value

        // Steering limit: only meaningful when engaged.
        steeringFilter.update(frame.steeringLimit ?? 0)
        smoothedSteeringLimit = steeringFilter.value

        updateFPS()
      case .settings:
        settings = DeviceSettings.decode(dict)
        // Once settings arrive we know the DongleId; ask for the visualisation
        // stream so the device starts pushing frames to us. Only request if we
        // haven't received any visualisation frames yet (avoid flooding).
        if framesReceived == 0 {
          requestVisualisation()
        }
      }
    } catch {
      AppLog.warn("msgpack decode failed: \(error) (\(data.count) bytes)")
    }
  }

  private func updateFPS() {
    let now = Date().timeIntervalSince1970
    frameTimestamps.append(now)
    // Keep only the last 2 seconds of timestamps.
    let cutoff = now - 2.0
    while let first = frameTimestamps.first, first < cutoff {
      frameTimestamps.removeFirst()
    }
    if frameTimestamps.count >= 2 {
      let span = frameTimestamps.last! - frameTimestamps.first!
      if span > 0 { fps = Double(frameTimestamps.count) / span }
    }
  }
}
