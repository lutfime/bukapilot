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

    // Handle every complete message that comes back from the device.
    ble.onMessage = { [weak self] channel, data in
      Task { @MainActor in self?.handle(channel: channel, data: data) }
    }

    // Auto-connect to the last known device on launch (no scan needed).
    // Small delay so the central manager has time to power on.
    DispatchQueue.main.asyncAfter(deadline: .now() + 0.5) { [weak self] in
      self?.ble.autoConnectIfKnown()
    }
  }

  // MARK: Public actions (called from views)

  func startScanning() { ble.startScan() }
  func stopScanning()  { ble.stopScan() }
  func connect(to id: UUID) { ble.connect(to: id) }
  func disconnect() { ble.disconnect() }

  /// Tell the device which channel the app is currently viewing.
  /// Mirrors `appbridged.py:handle_send_channel`'s `msgType: 'curPage'` flow.
  /// Phase 1 always wants the visualisation channel.
  func requestVisualisation() {
    let payload: [String: Any] = [
      "msgType": "curPage",
      "channel": Int(BLEProtocol.Channel.visualisation.rawValue),
    ]
    let blob = MsgpackEncoder.encode(payload)
    ble.send(blob)
  }

  // MARK: Message handling

  private func handle(channel: UInt8, data: Data) {
    guard let channel = BLEProtocol.Channel(rawValue: channel) else { return }
    do {
      let dict = try MsgpackDecoder.decodeMap(data)
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
        // stream so the device starts pushing frames to us.
        requestVisualisation()
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
