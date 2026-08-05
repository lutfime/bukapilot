import Foundation
import CoreBluetooth

/// BLE Nordic UART service + characteristic UUIDs.
/// Mirrors `selfdrive/appbridged/ble_helper.py`.
enum BLEProtocol {
  static let uartService      = CBUUID(string: "6E400001-B5A3-F393-E0A9-E50E24DCCA9E")
  static let rxCharacteristic = CBUUID(string: "6E400002-B5A3-F393-E0A9-E50E24DCCA9E") // phone → device (write)
  static let txCharacteristic = CBUUID(string: "6E400003-B5A3-F393-E0A9-E50E24DCCA9E") // device → phone (notify)

  /// Logical channel IDs from `appbridged.py`.
  enum Channel: UInt8 {
    case visualisation = 0x01
    case settings      = 0x02
  }

  /// Chunk header layout from `ble_helper.py` `chunk_and_send`.
  /// 4 bytes: [channel, msgId, totalSegments, segmentIndex]
  static let chunkHeaderSize = 4
  static let maxChunkPayload = 240
}

/// Connection state surfaced to the UI.
enum BLEConnectionState: Equatable {
  case disconnected
  case scanning
  case connecting
  case connected(deviceName: String)
  case failed(String)

  var isConnected: Bool {
    if case .connected = self { return true }
    return false
  }
}
