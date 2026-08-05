import Foundation

/// Reassembles BLE chunks into complete messages.
///
/// Direct port of `selfdrive/appbridged/ble_helper.py:ChunkReceiver`.
/// Each BLE write from the device carries a 4-byte header followed by payload:
///
///     [0] channel        (1 = visualisation, 2 = settings)
///     [1] msgId          (1..255, cycles)
///     [2] totalSegments  (1..255)
///     [3] segmentIndex   (0..totalSegments-1)
///     [4...] payload bytes (up to 240)
///
/// A message is "complete" when all `totalSegments` chunks for a given
/// (channel, msgId) pair have arrived; the payloads are then concatenated in
/// segment order and emitted. Incomplete messages are dropped after
/// `chunkTimeoutSeconds`.
final class ChunkReceiver {

  /// Emitted whenever a full message is reassembled.
  var onMessage: ((UInt8, Data) -> Void)?

  /// Drop incomplete messages after this long without a new segment.
  private let chunkTimeout: TimeInterval
  private let cleanupQueue = DispatchQueue(label: "kommu.chunkcleanup")
  private var active: [UInt16: Entry] = [:]

  /// (channel, msgId) packed into one UInt16 for cheap dictionary keys.
  private struct Entry {
    var chunks: [Data?]
    var missing: Int
    var lastSeen: TimeInterval
  }

  init(chunkTimeout: TimeInterval = 1.0) {
    self.chunkTimeout = chunkTimeout
  }

  /// Feed one raw BLE packet (as received from the TX notify characteristic).
  func feed(_ packet: Data) {
    guard packet.count >= BLEProtocol.chunkHeaderSize else { return }
    let bytes = [UInt8](packet)
    let channel   = bytes[0]
    let msgId     = bytes[1]
    let totalSegs = bytes[2]
    let segIdx    = bytes[3]

    guard totalSegs > 0, segIdx < totalSegs else { return }
    let payload = packet.subdata(in: BLEProtocol.chunkHeaderSize..<packet.count)
    let key = (UInt16(channel) << 8) | UInt16(msgId)
    let now = Date().timeIntervalSince1970

    cleanupQueue.sync {
      var entry: Entry
      if let existing = active[key] {
        entry = existing
        if entry.chunks.count != Int(totalSegs) {
          entry.chunks = Array(repeating: nil, count: Int(totalSegs))
          entry.missing = Int(totalSegs)
        }
      } else {
        entry = Entry(chunks: Array(repeating: nil, count: Int(totalSegs)),
                      missing: Int(totalSegs),
                      lastSeen: now)
      }

      if entry.chunks[Int(segIdx)] == nil { entry.missing -= 1 }
      entry.chunks[Int(segIdx)] = payload
      entry.lastSeen = now
      active[key] = entry

      if entry.missing == 0 {
        let joined = entry.chunks.compactMap { $0 }.reduce(into: Data()) { $0.append($1) }
        active.removeValue(forKey: key)
        DispatchQueue.main.async { self.onMessage?(channel, joined) }
      }

      // Opportunistic cleanup of stale partials.
      let stale = active.filter { now - $0.value.lastSeen > chunkTimeout }
      for k in stale.keys {
        AppLog.debug("dropping incomplete BLE message key=\(k)")
        active.removeValue(forKey: k)
      }
    }
  }

  /// Reset all in-flight reassembly state (e.g. on disconnect).
  func reset() {
    cleanupQueue.sync { active.removeAll() }
  }
}
