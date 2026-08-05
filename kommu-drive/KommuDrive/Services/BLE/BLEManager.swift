import Foundation
import CoreBluetooth

/// Central-role BLE manager for the KommuDrive app.
///
/// Responsibilities:
///   - Scan for peripherals advertising the Nordic UART service.
///   - Connect, discover the UART service, subscribe to TX notify.
///   - Forward incoming notify bytes to a `ChunkReceiver`.
///   - Expose a high-level `send(_:)` for writing command blobs to RX.
///
/// All `CBCentralManagerDelegate` / `CBPeripheralDelegate` callbacks are
/// marshalled onto `delegateQueue`. UI state is published via `@Published`
/// after hopping to the main thread.
final class BLEManager: NSObject {

  // MARK: Published state (read by SwiftUI)

  @Published private(set) var connectionState: BLEConnectionState = .disconnected
  @Published private(set) var discoveredPeripherals: [DiscoveredPeripheral] = []
  @Published private(set) var lastReceivedAt: Date?

  struct DiscoveredPeripheral: Identifiable, Equatable {
    let id: UUID
    let name: String
    let rssi: Int
    /// True if we matched on UART service or a KA2-style name; false if it's a
    /// generic nearby device surfaced by the "show all" fallback.
    var likelyKA2: Bool = true
  }

  /// Names we never want to show as connection candidates.
  private static let ignoredNames: Set<String> = [
    "n/a", "unknown", ""
  ]

  // MARK: Internals

  /// When true, the connection screen also lists every nearby device (not just
  /// ones we heuristically flagged as KA2). Flip this on if auto-match misses
  /// your device — you'll see its real advertised name and can tap to connect.
  @Published var showAllDevices: Bool = false

  /// Devices seen during the current scan that didn't match KA2 heuristics.
  /// Surfaced only when `showAllDevices` is on.
  @Published private(set) var otherPeripherals: [DiscoveredPeripheral] = []

  private let centralQueue = DispatchQueue(label: "kommu.ble.central")
  private var central: CBCentralManager!
  private var connectedPeripheral: CBPeripheral?
  private var rxChar: CBCharacteristic?
  private var txChar: CBCharacteristic?

  /// Reassembles incoming notify bytes into complete msgpack messages.
  let receiver = ChunkReceiver()

  /// Called on the main thread with a fully reassembled message.
  var onMessage: ((UInt8, Data) -> Void)? {
    didSet { receiver.onMessage = onMessage }
  }

  override init() {
    super.init()
    // Wire the chunk receiver before the central starts scanning so we never
    // miss the first packet after connect.
    receiver.onMessage = { [weak self] channel, data in
      guard let self else { return }
      DispatchQueue.main.async {
        self.lastReceivedAt = Date()
        self.onMessage?(channel, data)
      }
    }
    central = CBCentralManager(delegate: self, queue: centralQueue, options: [
      CBCentralManagerOptionShowPowerAlertKey: false,
    ])
  }

  // MARK: Public API

  /// Begin scanning for the KA2 device.
  ///
  /// The device advertises with `local_name = hostname` (e.g. "kommu-ka2-xxxx")
  /// but does **not** include the Nordic UART service UUID in its advertisement
  /// packet — it's only discoverable after connecting. (The kommu app scans the
  /// same way.) So we scan with no service filter and match candidates by:
  ///   (a) advertised UART service UUID, OR
  ///   (b) a hostname-style name (contains "kommu" or "ka2"), OR
  ///   (c) no filter at all in debug so we can see everything nearby.
  func startScan() {
    guard central.state == .poweredOn else {
      AppLog.warn("BLE not powered on (state=\(central.state.rawValue)); will scan when ready")
      DispatchQueue.main.async { self.connectionState = .scanning }
      return
    }
    DispatchQueue.main.async { self.connectionState = .scanning }
    discoveredPeripherals.removeAll()
    otherPeripherals.removeAll()
    // No service filter — device doesn't advertise UART in its packet.
    central.scanForPeripherals(
      withServices: nil,
      options: [CBCentralManagerScanOptionAllowDuplicatesKey: false]
    )
    AppLog.info("BLE scan started (no service filter)")
  }

  func stopScan() {
    central.stopScan()
    AppLog.info("BLE scan stopped")
  }

  /// Connect to a discovered peripheral by identifier.
  /// Looks in both the KA2 candidates list and the "other" fallback list.
  func connect(to id: UUID) {
    let peripheral = discoveredPeripherals.first(where: { $0.id == id })
                ?? otherPeripherals.first(where: { $0.id == id })
    guard let peripheral else {
      AppLog.warn("connect: peripheral \(id) not in discovered list")
      return
    }
    // Re-resolve through central to get a CBPeripheral we own.
    stopScan()
    let p = central.retrievePeripherals(withIdentifiers: [id]).first
      ?? connectedPeripheral
      ?? peripheralForIdentifier(id)
    let target = p ?? peripheralForIdentifier(id)
    connect(peripheral: target, name: peripheral.name)
  }

  /// Disconnect the current peripheral.
  func disconnect() {
    if let p = connectedPeripheral {
      central.cancelPeripheralConnection(p)
    }
    connectedPeripheral = nil
    rxChar = nil
    txChar = nil
    receiver.reset()
    DispatchQueue.main.async { self.connectionState = .disconnected }
  }

  /// Send a complete msgpack command blob to the RX characteristic.
  /// The blob should already be unchunked; appbridged reassembles on its side.
  func send(_ data: Data) {
    guard let p = connectedPeripheral, let rx = rxChar else {
      AppLog.warn("send: not connected / RX char missing")
      return
    }
    p.writeValue(data, for: rx, type: .withResponse)
  }

  // MARK: Helpers

  private func connect(peripheral: CBPeripheral?, name: String) {
    guard let peripheral else {
      DispatchQueue.main.async { self.connectionState = .failed("peripheral not found") }
      return
    }
    connectedPeripheral = peripheral
    peripheral.delegate = self
    DispatchQueue.main.async { self.connectionState = .connecting }
    central.connect(peripheral, options: nil)
    AppLog.info("connecting to \(name)")
  }

  /// Fallback resolver: CoreBluetooth sometimes needs retrievePeripherals
  /// instead of the cached discovered object.
  private func peripheralForIdentifier(_ id: UUID) -> CBPeripheral? {
    central.retrievePeripherals(withIdentifiers: [id]).first
  }
}

// MARK: - CBCentralManagerDelegate

extension BLEManager: CBCentralManagerDelegate {

  func centralManagerDidUpdateState(_ central: CBCentralManager) {
    AppLog.info("central state = \(central.state.rawValue)")
    if central.state == .poweredOn, case .scanning = connectionState {
      startScan()
    }
  }

  func centralManager(_ central: CBCentralManager,
                      didDiscover peripheral: CBPeripheral,
                      advertisementData: [String: Any],
                      rssi RSSI: NSNumber) {
    let name = (advertisementData[CBAdvertisementDataLocalNameKey] as? String)
              ?? peripheral.name
              ?? "Unknown"
    let services = (advertisementData[CBAdvertisementDataServiceUUIDsKey] as? [CBUUID]) ?? []
    let serviceUUIDs = services.map { $0.uuidString }

    // Always log so we can see what's actually nearby when debugging.
    AppLog.debug("discovered: name=\(name) rssi=\(RSSI.intValue) services=\(serviceUUIDs) id=\(peripheral.identifier.uuidString.prefix(8))")

    // Candidate if: advertises UART, OR name looks like a KA2/kommu hostname,
    // OR has a non-generic name (the device hostname is often a serial-ish
    // string like "lp-..." that doesn't contain "kommu").
    let advertisesUART = services.contains(where: { $0 == BLEProtocol.uartService })
    let lower = name.lowercased()
    let looksLikeKA2 = lower.contains("kommu") || lower.contains("ka2")
    let isGeneric = Self.ignoredNames.contains(lower)
    let likelyKA2 = (advertisesUART || looksLikeKA2) && !isGeneric

    let entry = DiscoveredPeripheral(id: peripheral.identifier, name: name,
                                     rssi: RSSI.intValue, likelyKA2: likelyKA2)
    DispatchQueue.main.async {
      if likelyKA2 {
        if !self.discoveredPeripherals.contains(where: { $0.id == entry.id }) {
          self.discoveredPeripherals.append(entry)
        }
      } else if !isGeneric {
        if !self.otherPeripherals.contains(where: { $0.id == entry.id }) {
          self.otherPeripherals.append(entry)
        }
      }
    }
  }

  func centralManager(_ central: CBCentralManager, didConnect peripheral: CBPeripheral) {
    let name = peripheral.name ?? "KA2"
    AppLog.info("connected to \(name), discovering services")
    DispatchQueue.main.async { self.connectionState = .connected(deviceName: name) }
    peripheral.discoverServices([BLEProtocol.uartService])
  }

  func centralManager(_ central: CBCentralManager,
                      didFailToConnect peripheral: CBPeripheral,
                      error: Error?) {
    let msg = error?.localizedDescription ?? "unknown"
    AppLog.error("didFailToConnect: \(msg)")
    DispatchQueue.main.async { self.connectionState = .failed(msg) }
  }

  func centralManager(_ central: CBCentralManager,
                      didDisconnectPeripheral peripheral: CBPeripheral,
                      error: Error?) {
    let msg = error?.localizedDescription
    AppLog.warn("disconnected: \(msg ?? "clean")")
    rxChar = nil
    txChar = nil
    receiver.reset()
    let state: BLEConnectionState = msg.map { .failed($0) } ?? .disconnected
    DispatchQueue.main.async { self.connectionState = state }
  }
}

// MARK: - CBPeripheralDelegate

extension BLEManager: CBPeripheralDelegate {

  func peripheral(_ peripheral: CBPeripheral,
                  didDiscoverServices error: Error?) {
    if let error { AppLog.error("didDiscoverServices: \(error)"); return }
    guard let service = peripheral.services?.first(where: { $0.uuid == BLEProtocol.uartService }) else {
      AppLog.error("UART service not found on peripheral")
      return
    }
    peripheral.discoverCharacteristics([BLEProtocol.rxCharacteristic, BLEProtocol.txCharacteristic],
                                       for: service)
  }

  func peripheral(_ peripheral: CBPeripheral,
                  didDiscoverCharacteristicsFor service: CBService,
                  error: Error?) {
    if let error { AppLog.error("didDiscoverCharacteristics: \(error)"); return }
    for char in service.characteristics ?? [] {
      if char.uuid == BLEProtocol.txCharacteristic {
        txChar = char
        // Subscribe to notifications — this is how the device pushes frames.
        peripheral.setNotifyValue(true, for: char)
        AppLog.info("subscribed to TX notify")
      } else if char.uuid == BLEProtocol.rxCharacteristic {
        rxChar = char
      }
    }
  }

  func peripheral(_ peripheral: CBPeripheral,
                  didUpdateValueFor characteristic: CBCharacteristic,
                  error: Error?) {
    if let error { AppLog.error("didUpdateValueFor: \(error)"); return }
    guard let data = characteristic.value else { return }
    receiver.feed(data)
  }

  func peripheral(_ peripheral: CBPeripheral,
                  didUpdateNotificationStateFor characteristic: CBCharacteristic,
                  error: Error?) {
    if let error { AppLog.error("notify state: \(error)"); return }
    AppLog.debug("notify \(characteristic.uuid == BLEProtocol.txCharacteristic ? "TX on" : "?")")
  }
}
