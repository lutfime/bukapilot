import Foundation

/// Native Swift parser for openpilot qlog files (Cap'n Proto binary format).
/// Uses libzstd (statically linked) for decompression and a hand-rolled
/// Cap'n Proto wire format reader for parsing events.
///
/// The qlog is a flat concatenation of unframed Cap'n Proto messages, zstd-compressed.
/// Each message contains an `Event` struct with a union discriminant.
enum QlogParser {

  // MARK: - Event struct layout (verified empirically against real qlog data)
  // Event struct: [data 0-7][logMonoTime 8-15][discriminant 16-17][pad 18-23][pointer 24-31]
  private static let lmtOffset = 8       // UInt64 logMonoTime
  private static let discOffset = 16     // UInt16 union discriminant
  private static let ptrOffset = 24      // near struct pointer to union member
  private static let discCarState: UInt16 = 21
  private static let discControlsState: UInt16 = 6

  // CarState field byte offsets (verified empirically: vEgo at byte 8, torque at byte 28)
  private static let csVEgo = 8       // Float32
  private static let csSteerTorque = 28  // Float32
  private static let csSteerPressed = 66 // Bool bit offset (TBD)

  // ControlsState field byte offsets
  private static let ctrlCurvature = 136       // Float32 (slot 34 * 4)
  private static let ctrlDesiredCurvature = 176 // Float32 (slot 44 * 4)

  // MARK: - Cap'n Proto wire format reading

  /// Reads a message from the flat message stream.
  /// Returns (structStart, nextMessageOffset) or nil.
  private static func readMessage(_ d: Data, off: Int) -> (Int, Int)? {
    guard off + 4 <= d.count else { return nil }
    let segCount = Int(readU32(d, off)) + 1
    guard segCount >= 1, segCount <= 20 else { return nil }
    var headerSize = 4 + segCount * 4
    var totalWords = 0
    for i in 0..<segCount {
      totalWords += Int(readU32(d, off + 4 + i * 4))
    }
    headerSize = (headerSize + 7) & ~7
    let structStart = off + headerSize
    let nextOff = structStart + totalWords * 8
    guard nextOff <= d.count else { return nil }
    return (structStart, nextOff)
  }

  /// Follows a near pointer (8 bytes) to the target struct. Returns absolute byte offset.
  private static func followPtr(_ d: Data, ptrOff: Int) -> Int? {
    guard ptrOff + 8 <= d.count else { return nil }
    let raw = readU64(d, ptrOff)
    guard raw & 3 == 0 else { return nil } // type 0 = struct
    let offBits = (raw >> 2) & 0x3FFFFFFF
    let offWords = offBits >= 0x2000000 ? Int(offBits) - 0x4000000 : Int(offBits)
    return ptrOff + offWords * 8
  }

  private static func readU16(_ d: Data, _ o: Int) -> UInt16 {
    guard o + 2 <= d.count else { return 0 }
    return d.withUnsafeBytes { $0.loadUnaligned(fromByteOffset: o, as: UInt16.self) }
  }
  private static func readU32(_ d: Data, _ o: Int) -> UInt32 {
    guard o + 4 <= d.count else { return 0 }
    return d.withUnsafeBytes { $0.loadUnaligned(fromByteOffset: o, as: UInt32.self) }
  }
  private static func readU64(_ d: Data, _ o: Int) -> UInt64 {
    guard o + 8 <= d.count else { return 0 }
    return d.withUnsafeBytes { $0.loadUnaligned(fromByteOffset: o, as: UInt64.self) }
  }
  private static func readF32(_ d: Data, _ o: Int) -> Float {
    guard o + 4 <= d.count else { return 0 }
    return Float(bitPattern: readU32(d, o))
  }
  private static func readBool(_ d: Data, bitOff: Int) -> Bool {
    let b = bitOff / 8
    guard b < d.count else { return false }
    return (d[b] & (1 << (bitOff % 8))) != 0
  }

  // MARK: - zstd decompression (via libzstd static library)

  /// Decompresses zstd data using libzstd.
  static func decompressZstd(_ data: Data) -> Data? {
    return data.withUnsafeBytes { (inPtr: UnsafeRawBufferPointer) -> Data? in
      let inBuf = inPtr.baseAddress!
      let inSize = data.count

      // Get decompressed size
      let outSize = ZSTD_getFrameContentSize(inBuf, inSize)
      guard outSize != UInt64.max, outSize != UInt64.max - 1 else {
        return decompressZstdStream(data, estimatedSize: inSize * 20)
      }

      var output = Data(count: Int(outSize))
      let result = output.withUnsafeMutableBytes { (outPtr: UnsafeMutableRawBufferPointer) -> Int in
        Int(ZSTD_decompress(outPtr.baseAddress!, Int(outSize), inBuf, inSize))
      }

      if result > 0 && ZSTD_isError(numericCast(result)) == 0 {
        return output.prefix(result)
      }
      return nil
    }
  }

  private static func decompressZstdStream(_ data: Data, estimatedSize: Int) -> Data? {
    var output = Data(count: estimatedSize)
    let result = data.withUnsafeBytes { inPtr -> Int in
      output.withUnsafeMutableBytes { outPtr -> Int in
        Int(ZSTD_decompress(outPtr.baseAddress!, estimatedSize, inPtr.baseAddress!, data.count))
      }
    }
    if result > 0 && ZSTD_isError(numericCast(result)) == 0 {
      return output.prefix(result)
    }
    return nil
  }

  // MARK: - Main parse

  /// Parses a compressed qlog into DriveData.
  static func parse(_ compressed: Data, routeStartTime: Double = 0) -> DriveData {
    // Decompress
    let data: Data
    if compressed.count >= 4 {
      let magic = readU32(compressed, 0)
      if magic == 0xFD2FB528 {
        guard let dec = decompressZstd(compressed) else { return DriveData() }
        data = dec
      } else {
        data = compressed
      }
    } else {
      data = compressed
    }

    var result = DriveData()
    var firstMono: Double = 0
    var lastV: Double = 0, lastTrq: Double = 0, lastPressed = false

    var off = 0
    while off < data.count {
      guard let msg = readMessage(data, off: off) else { break }
      let s = msg.0

      let disc = readU16(data, s + discOffset)
      let mono = Double(readU64(data, s + lmtOffset)) / 1e9
      if firstMono == 0 { firstMono = mono }

      if disc == discCarState {
        if let t = followPtr(data, ptrOff: s + ptrOffset) {
          lastV = Double(readF32(data, t + csVEgo))
          lastTrq = Double(readF32(data, t + csSteerTorque))
          lastPressed = readBool(data, bitOff: t * 8 + csSteerPressed)
        }
      } else if disc == discControlsState {
        if let t = followPtr(data, ptrOff: s + ptrOffset) {
          let tWall = routeStartTime + (mono - firstMono)
          result.t.append((tWall * 100).rounded() / 100)
          result.v.append((lastV * 100).rounded() / 100)
          result.o.append(lastPressed ? 1 : 0)
          result.trq.append((lastTrq * 10).rounded() / 10)
          result.d.append((Double(readF32(data, t + ctrlDesiredCurvature)) * 1e5).rounded() / 1e5)
          result.a.append((Double(readF32(data, t + ctrlCurvature)) * 1e5).rounded() / 1e5)
          // PID/torque fields — TODO: read via nested pointer (not yet verified)
          result.c.append(0); result.p.append(0); result.i.append(0)
          result.f.append(0); result.sat.append(0); result.eng.append(0)
        }
      }

      off = msg.1
    }

    return result
  }
}
