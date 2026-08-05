import Foundation

/// Minimal MessagePack (msgpack) decoder.
///
/// Phase 1 only needs to decode the map-of-mixed-types payloads that
/// `appbridged.py` produces via `msgpack.packb(...)`. This covers the format
/// family actually used there:
///   - nil, bool, int (positive + negative fixint, uint8/16/32/64, int8/16/32/64)
///   - float32/64
///   - fixstr / str8 / str16 / str32
///   - fixarray / array16 / array32
///   - fixmap / map16 / map32
///   - bin8/16/32  (decoded as Data)
///
/// Unsupported extensions throw. Errors are surfaced; callers should catch and
/// drop the frame rather than crash — a malformed packet must never break the UI.
enum MsgpackError: Error { case truncated, unsupportedFormat(UInt8) }

enum MsgpackDecoder {
  /// Decode a msgpack blob into a top-level value.
  /// Returns one of: nil, Bool, Int, Double, String, Data, [Any], [String: Any].
  static func decode(_ data: Data) throws -> Any? {
    var idx = 0
    return try parse(data, &idx)
  }

  /// Convenience: decode and require a map at the top level.
  static func decodeMap(_ data: Data) throws -> [String: Any] {
    guard let value = try decode(data) else { return [:] }
    if let dict = value as? [String: Any] { return dict }
    return [:]
  }

  // MARK: - Internals

  private static func parse(_ data: Data, _ idx: inout Int) throws -> Any? {
    guard idx < data.count else { throw MsgpackError.truncated }
    let b = data[idx]; idx += 1

    switch b {
    // nil / bool
    case 0xC0: return nil
    case 0xC2: return false
    case 0xC3: return true

    // positive fixint (0x00..0x7f) already covered by fallthrough default
    case 0xCC: return Int(try byte(data, &idx))                    // uint8
    case 0xCD: return Int(try u16(data, &idx))                     // uint16
    case 0xCE: return Int(try u32(data, &idx))                     // uint32
    case 0xCF: return Int(try u64(data, &idx))                     // uint64 (best effort)

    // negative fixint (0xe0..0xff): signed value = b - 256
    case 0xD0: return Int(Int8(bitPattern: try byte(data, &idx)))  // int8
    case 0xD1: return Int(Int16(bitPattern: try u16(data, &idx)))  // int16
    case 0xD2: return Int(Int32(bitPattern: try u32(data, &idx)))  // int32
    case 0xD3: return Int(Int64(bitPattern: try u64(data, &idx)))  // int64 (best effort)

    // floats
    case 0xCA: return Double(Float(bitPattern: try u32(data, &idx))) // float32
    case 0xCB: return Double(bitPattern: try u64(data, &idx))        // float64

    // strings
    case 0xA0...0xBF: return try string(data, &idx, len: Int(b & 0x1F))           // fixstr
    case 0xD9: return try string(data, &idx, len: Int(try byte(data, &idx)))      // str8
    case 0xDA: return try string(data, &idx, len: Int(try u16(data, &idx)))       // str16
    case 0xDB: return try string(data, &idx, len: Int(try u32(data, &idx)))       // str32

    // binary
    case 0xC4: return try bin(data, &idx, len: Int(try byte(data, &idx)))         // bin8
    case 0xC5: return try bin(data, &idx, len: Int(try u16(data, &idx)))          // bin16
    case 0xC6: return try bin(data, &idx, len: Int(try u32(data, &idx)))          // bin32

    // arrays
    case 0x90...0x9F: return try array(data, &idx, count: Int(b & 0x0F))          // fixarray
    case 0xDC: return try array(data, &idx, count: Int(try u16(data, &idx)))      // array16
    case 0xDD: return try array(data, &idx, count: Int(try u32(data, &idx)))      // array32

    // maps
    case 0x80...0x8F: return try map(data, &idx, count: Int(b & 0x0F))            // fixmap
    case 0xDE: return try map(data, &idx, count: Int(try u16(data, &idx)))        // map16
    case 0xDF: return try map(data, &idx, count: Int(try u32(data, &idx)))        // map32

    // positive fixint
    case 0x00...0x7F: return Int(b)
    // negative fixint
    case 0xE0...0xFF: return Int(Int8(bitPattern: b))

    default: throw MsgpackError.unsupportedFormat(b)
    }
  }

  // MARK: primitive readers

  private static func byte(_ data: Data, _ idx: inout Int) throws -> UInt8 {
    guard idx < data.count else { throw MsgpackError.truncated }
    let v = data[idx]; idx += 1; return v
  }
  private static func u16(_ data: Data, _ idx: inout Int) throws -> UInt16 {
    guard idx + 2 <= data.count else { throw MsgpackError.truncated }
    let v = (UInt16(data[idx]) << 8) | UInt16(data[idx+1]); idx += 2; return v
  }
  private static func u32(_ data: Data, _ idx: inout Int) throws -> UInt32 {
    guard idx + 4 <= data.count else { throw MsgpackError.truncated }
    var v: UInt32 = 0
    for i in 0..<4 { v = (v << 8) | UInt32(data[idx+i]) }
    idx += 4; return v
  }
  private static func u64(_ data: Data, _ idx: inout Int) throws -> UInt64 {
    guard idx + 8 <= data.count else { throw MsgpackError.truncated }
    var v: UInt64 = 0
    for i in 0..<8 { v = (v << 8) | UInt64(data[idx+i]) }
    idx += 8; return v
  }

  private static func string(_ data: Data, _ idx: inout Int, len: Int) throws -> String {
    guard idx + len <= data.count else { throw MsgpackError.truncated }
    let slice = data.subdata(in: idx..<(idx+len))
    idx += len
    return String(data: slice, encoding: .utf8) ?? ""
  }
  private static func bin(_ data: Data, _ idx: inout Int, len: Int) throws -> Data {
    guard idx + len <= data.count else { throw MsgpackError.truncated }
    let slice = data.subdata(in: idx..<(idx+len)); idx += len; return slice
  }
  private static func array(_ data: Data, _ idx: inout Int, count: Int) throws -> [Any] {
    var out: [Any] = []; out.reserveCapacity(count)
    for _ in 0..<count { if let v = try parse(data, &idx) { out.append(v) } }
    return out
  }
  private static func map(_ data: Data, _ idx: inout Int, count: Int) throws -> [String: Any] {
    var out: [String: Any] = [:]; out.reserveCapacity(count)
    for _ in 0..<count {
      guard let key = try parse(data, &idx) else { continue }
      let value = try parse(data, &idx)
      // Keys in appbridged payloads are always strings; coerce defensively.
      let keyString: String
      if let s = key as? String { keyString = s }
      else if let n = key as? Int { keyString = String(n) }
      else { keyString = String(describing: key) }
      out[keyString] = value
    }
    return out
  }
}

/// Encoder for the very small subset needed by Phase 1 commands:
/// we only need to send `{ "msgType": "curPage", "channel": <int> }`-style maps.
/// Nil / bool / int / double / string values are supported.
enum MsgpackEncoder {
  static func encode(_ value: Any) -> Data {
    var out = Data()
    encodeInto(value, &out)
    return out
  }

  private static func encodeInto(_ value: Any, _ out: inout Data) {
    switch value {
    case is NSNull, is Optional<NSNull>:
      out.append(0xC0)
    case let v as Bool:
      out.append(v ? 0xC3 : 0xC2)
    case let v as Int:
      encodeInt(v, &out)
    case let v as Double:
      out.append(0xCB)
      var bits = v.bitPattern.bigEndian
      out.append(Data(bytes: &bits, count: 8))
    case let v as Float:
      out.append(0xCA)
      var bits = v.bitPattern.bigEndian
      out.append(Data(bytes: &bits, count: 4))
    case let v as String:
      encodeStr(v, &out)
    case let v as [Any]:
      encodeArrayHeader(count: v.count, &out)
      for item in v { encodeInto(item, &out) }
    case let v as [String: Any]:
      encodeMapHeader(count: v.count, &out)
      for (k, val) in v {
        encodeStr(k, &out)
        encodeInto(val, &out)
      }
    default:
      // Fallback: encode as nil so the device doesn't choke.
      out.append(0xC0)
    }
  }

  private static func encodeInt(_ v: Int, _ out: inout Data) {
    if v >= 0 && v <= 0x7F {
      out.append(UInt8(v))
    } else if v >= 0 && v <= 0xFF {
      out.append(0xCC); out.append(UInt8(v))
    } else if v >= 0 && v <= 0xFFFF {
      out.append(0xCD); out.append(UInt8(v >> 8)); out.append(UInt8(v & 0xFF))
    } else if v < 0 && v >= -32 {
      out.append(UInt8(bitPattern: Int8(v)))
    } else if v >= -128 && v < 0 {
      out.append(0xD0); out.append(UInt8(bitPattern: Int8(v)))
    } else {
      // int16 for everything else in Phase 1 (values are small enums/page ids)
      out.append(0xD1)
      let n = Int16(v).bigEndian
      withUnsafeBytes(of: n) { out.append(contentsOf: $0) }
    }
  }
  private static func encodeStr(_ v: String, _ out: inout Data) {
    let bytes = Array(v.utf8)
    if bytes.count <= 31 {
      out.append(0xA0 | UInt8(bytes.count))
    } else if bytes.count <= 0xFF {
      out.append(0xD9); out.append(UInt8(bytes.count))
    } else {
      out.append(0xDA)
      let n = UInt16(bytes.count).bigEndian
      withUnsafeBytes(of: n) { out.append(contentsOf: $0) }
    }
    out.append(contentsOf: bytes)
  }
  private static func encodeArrayHeader(count: Int, _ out: inout Data) {
    if count <= 15 { out.append(0x90 | UInt8(count)) }
    else { out.append(0xDC); let n = UInt16(count).bigEndian; withUnsafeBytes(of: n) { out.append(contentsOf: $0) } }
  }
  private static func encodeMapHeader(count: Int, _ out: inout Data) {
    if count <= 15 { out.append(0x80 | UInt8(count)) }
    else { out.append(0xDE); let n = UInt16(count).bigEndian; withUnsafeBytes(of: n) { out.append(contentsOf: $0) } }
  }
}
