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
    var name: String
    var rssi: Int
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

  /// True once we've connected to a device at least once — enables auto-reconnect
  /// on unexpected disconnects and auto-connect on app launch.
  @Published var autoReconnectEnabled: Bool = false

  /// Set to true when a reconnect/auto-connect attempt has been stuck in
  /// `.connecting` for too long. The UI uses this to stop showing the
  /// reconnect overlay and fall through to the ConnectionView scan screen.
  @Published var reconnectTimedOut: Bool = false

  private let centralQueue = DispatchQueue(label: "kommu.ble.central")
  private var central: CBCentralManager!
  private var connectedPeripheral: CBPeripheral?
  private var rxChar: CBCharacteristic?
  private var txChar: CBCharacteristic?

  /// Tracks device IDs we've already logged so the debug log isn't spammed by
  /// the allow-duplicates scan. Reset on each startScan().
  private var seenLogIds: Set<String> = []

  /// Persisted identifier of the last successfully connected device, so we can
  /// auto-reconnect after disconnects and auto-connect on app launch.
  private static let lastDeviceKey = "kommu.lastDeviceID"
  private var lastConnectedID: UUID? {
    get { UserDefaults.standard.string(forKey: Self.lastDeviceKey).flatMap(UUID.init) }
    set {
      if let id = newValue { UserDefaults.standard.set(id.uuidString, forKey: Self.lastDeviceKey) }
      else { UserDefaults.standard.removeObject(forKey: Self.lastDeviceKey) }
    }
  }

  /// Reconnect backoff (seconds). Doubles on each failure, capped at 30s.
  private var reconnectAttempt = 0
  private var reconnectWorkItem: DispatchWorkItem?

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
    seenLogIds.removeAll()

    // Stage 1: check for peripherals the system already knows about. If the
    // kommu app (or system) has a connection/cache, this finds it instantly
    // without needing the device to be advertising.
    let known = central.retrieveConnectedPeripherals(withServices: [BLEProtocol.uartService])
    for p in known {
      AppLog.info("retrieveConnectedPeripherals hit: \(p.name ?? "?")")
      addCandidate(p, rssi: -40, advertisedUART: true)
    }

    // Stage 2: active scan. Allow duplicates so we catch the scan-response
    // packet on the second sighting — the KA2 only sends its name in the scan
    // response, not the primary advertisement, so the first packet is "Unknown".
    central.scanForPeripherals(
      withServices: nil,
      options: [CBCentralManagerScanOptionAllowDuplicatesKey: true]
    )
    AppLog.info("BLE scan started (no service filter, allow duplicates)")
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

  /// Auto-connect to the last known device, if any. Call on app launch.
  /// Resolves via retrievePeripherals (works without scanning) and connects
  /// directly; if the device isn't reachable, CoreBluetooth will fail and we
  /// fall through to the normal scan flow.
  func autoConnectIfKnown() {
    guard autoReconnectEnabled, let id = lastConnectedID else {
      // No saved device — start scanning so the user sees the ConnectionView list.
      startScan()
      return
    }
    guard let p = central.retrievePeripherals(withIdentifiers: [id]).first else {
      AppLog.info("autoConnect: last device \(id) not known to system; starting scan")
      // Device not resolvable (e.g. KA2 rebooted, BLE address changed) — fall
      // back to active scanning so the user can re-discover and reconnect.
      startScan()
      return
    }
    AppLog.info("autoConnect: connecting to last device \(p.name ?? id.uuidString.prefix(8).description)")
    connect(peripheral: p, name: p.name ?? "KA2")
  }

  /// Forget the last connected device and stop auto-reconnecting.
  func forgetDevice() {
    lastConnectedID = nil
    autoReconnectEnabled = false
    cancelReconnect()
    disconnect()
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

  /// Send a msgpack payload to the device on the given channel.
  ///
  /// The device's `ChunkReceiver` expects every BLE write to carry a 4-byte
  /// chunk header: [channel, msgId, totalSegments, segmentIndex] followed by
  /// up to 240 payload bytes. For payloads ≤ 240 bytes (all our commands), this
  /// is a single chunk: header = [channel, msgId, 1, 0].
  func send(_ data: Data, channel: UInt8 = 0x02) {
    guard let p = connectedPeripheral, let rx = rxChar else {
      AppLog.warn("send: not connected / RX char missing")
      return
    }

    // Build chunked packets. Most commands fit in one 240-byte chunk.
    let chunkSize = BLEProtocol.maxChunkPayload
    let totalSegs = UInt8(min(255, (data.count + chunkSize - 1) / chunkSize))
    let msgId = UInt8.random(in: 1...255)

    for segIdx in 0..<Int(totalSegs) {
      let offset = segIdx * chunkSize
      let end = min(offset + chunkSize, data.count)
      var packet = Data()
      packet.append(channel)
      packet.append(msgId)
      packet.append(totalSegs)
      packet.append(UInt8(segIdx))
      packet.append(data.subdata(in: offset..<end))
      p.writeValue(packet, for: rx, type: .withResponse)
    }
  }

  // MARK: Helpers

  private func connect(peripheral: CBPeripheral?, name: String) {
    guard let peripheral else {
      DispatchQueue.main.async { self.connectionState = .failed("peripheral not found") }
      return
    }
    connectedPeripheral = peripheral
    peripheral.delegate = self
    lastConnectedID = peripheral.identifier   // remember for auto-reconnect
    cancelReconnect()
    DispatchQueue.main.async {
      self.connectionState = .connecting
      self.reconnectTimedOut = false
    }
    central.connect(peripheral, options: nil)
    AppLog.info("connecting to \(name)")

    // Timeout: if we're still in .connecting after 8 seconds, bail out to
    // the scan screen so the user isn't stuck on a frozen reconnect overlay.
    centralQueue.asyncAfter(deadline: .now() + 8) { [weak self] in
      guard let self else { return }
      if case .connecting = self.connectionState {
        AppLog.warn("connect timed out after 8s — falling back to scan")
        DispatchQueue.main.async {
          self.reconnectTimedOut = true
          self.connectionState = .disconnected
          self.cancelPeripheralConnection()
          self.startScan()
        }
      }
    }
  }

  private func cancelPeripheralConnection() {
    if let p = connectedPeripheral {
      central.cancelPeripheralConnection(p)
    }
    connectedPeripheral = nil
    rxChar = nil
    txChar = nil
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
    let advertisesUART = services.contains(where: { $0 == BLEProtocol.uartService })

    // Verbose log on first sighting of each device.
    let idPrefix = peripheral.identifier.uuidString.prefix(8)
    let serviceUUIDs = services.map { $0.uuidString }
    if seenLogIds.insert(String(idPrefix)).inserted {
      AppLog.debug("discovered: name=\(name) rssi=\(RSSI.intValue) services=\(serviceUUIDs) id=\(idPrefix)")
    }
    addCandidate(peripheral, rssi: RSSI.intValue, name: name, advertisedUART: advertisesUART)
  }

  /// Shared add/update for both scan hits and retrieveConnectedPeripherals hits.
  /// Updates the name if a later packet carries the real advertised name (the
  /// KA2 sends its name in the scan response, so the first packet is "Unknown").
  private func addCandidate(_ peripheral: CBPeripheral,
                            rssi: Int,
                            name: String? = nil,
                            advertisedUART: Bool) {
    let resolvedName = name ?? peripheral.name ?? "Unknown"
    let lower = resolvedName.lowercased()
    let looksLikeKA2 = lower.contains("kommu") || lower.contains("ka2")
    let isGeneric = Self.ignoredNames.contains(lower)
    let isStrongUnknown = isGeneric && rssi >= -60
    let likelyKA2 = (advertisedUART || looksLikeKA2 || isStrongUnknown) && true

    // AUTO-CONNECT: if we have a saved device ID and this peripheral matches,
    // connect immediately without requiring the user to tap.
    if autoReconnectEnabled,
       let savedID = lastConnectedID,
       peripheral.identifier == savedID,
       connectedPeripheral == nil,
       !connectionState.isConnected {
      AppLog.info("autoConnect: found saved device \(resolvedName) in scan, connecting")
      stopScan()
      connect(peripheral: peripheral, name: resolvedName)
      return
    }

    let entry = DiscoveredPeripheral(id: peripheral.identifier, name: resolvedName,
                                     rssi: rssi, likelyKA2: likelyKA2)
    DispatchQueue.main.async {
      // If we already have this peripheral, prefer the better name / stronger RSSI.
      if let existingIdx = self.discoveredPeripherals.firstIndex(where: { $0.id == entry.id }) {
        let existing = self.discoveredPeripherals[existingIdx]
        let betterName = (existing.name == "Unknown" && resolvedName != "Unknown") ? resolvedName : existing.name
        self.discoveredPeripherals[existingIdx].name = betterName
        self.discoveredPeripherals[existingIdx].rssi = rssi
      } else if let otherIdx = self.otherPeripherals.firstIndex(where: { $0.id == entry.id }) {
        // Upgrade from "other" → "likely KA2" if new info says so.
        if likelyKA2 {
          self.otherPeripherals.remove(at: otherIdx)
          self.discoveredPeripherals.append(entry)
        } else {
          self.otherPeripherals[otherIdx].rssi = rssi
        }
      } else {
        if likelyKA2 {
          self.discoveredPeripherals.append(entry)
        } else if !isGeneric {
          self.otherPeripherals.append(entry)
        }
      }
    }
  }

  func centralManager(_ central: CBCentralManager, didConnect peripheral: CBPeripheral) {
    let name = peripheral.name ?? "KA2"
    AppLog.info("connected to \(name), discovering services")
    reconnectAttempt = 0                  // reset backoff on success
    autoReconnectEnabled = true           // first connect arms auto-reconnect
    DispatchQueue.main.async { self.connectionState = .connected(deviceName: name) }
    peripheral.discoverServices([BLEProtocol.uartService])
  }

  func centralManager(_ central: CBCentralManager,
                      didFailToConnect peripheral: CBPeripheral,
                      error: Error?) {
    let msg = error?.localizedDescription ?? "unknown"
    AppLog.error("didFailToConnect: \(msg)")
    DispatchQueue.main.async { self.connectionState = .failed(msg) }
    scheduleReconnect()                   // try again after backoff
  }

  func centralManager(_ central: CBCentralManager,
                      didDisconnectPeripheral peripheral: CBPeripheral,
                      error: Error?) {
    let msg = error?.localizedDescription
    AppLog.warn("disconnected: \(msg ?? "clean")")
    rxChar = nil
    txChar = nil
    receiver.reset()
    // Show a transient state, then attempt reconnect if armed.
    DispatchQueue.main.async { self.connectionState = .disconnected }
    scheduleReconnect()
  }

  // MARK: Reconnect

  /// Attempt to reconnect to the last device with exponential backoff.
  /// Only fires when autoReconnectEnabled is on and we have a remembered ID.
  private func scheduleReconnect() {
    cancelReconnect()
    guard autoReconnectEnabled, let id = lastConnectedID else { return }

    reconnectAttempt += 1
    let delay = min(2.0 * Double(reconnectAttempt), 30.0)  // 2s, 4s, 8s, ... cap 30s
    AppLog.info("scheduling reconnect attempt #\(reconnectAttempt) in \(delay)s")
    DispatchQueue.main.async { self.connectionState = .connecting }

    let work = DispatchWorkItem { [weak self] in
      guard let self else { return }
      // After 5 failed attempts, give up on reconnect and fall back to scanning
      // so the user can re-discover the device manually.
      if self.reconnectAttempt > 5 {
        AppLog.warn("reconnect: gave up after \(self.reconnectAttempt) attempts; starting scan")
        self.reconnectAttempt = 0
        self.connectionState = .disconnected
        self.startScan()
        return
      }
      guard let p = self.central.retrievePeripherals(withIdentifiers: [id]).first else {
        AppLog.warn("reconnect: device \(id) no longer resolvable (attempt \(self.reconnectAttempt))")
        self.scheduleReconnect()
        return
      }
      AppLog.info("reconnect: connecting to \(p.name ?? "?")")
      self.connectedPeripheral = p
      p.delegate = self
      self.central.connect(p, options: nil)
    }
    reconnectWorkItem = work
    centralQueue.asyncAfter(deadline: .now() + delay, execute: work)
  }

  private func cancelReconnect() {
    reconnectWorkItem?.cancel()
    reconnectWorkItem = nil
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
