//
//  QlogParserTests.swift
//  Tests native Swift qlog parser against the real qlog file.
//  Source: result/drives/2026-08-04--03-38-17.qlog.zst
//

import XCTest
@testable import KommuDrive

final class QlogParserTests: XCTestCase {

  /// Loads the real qlog file from the test bundle.
  private func loadRealQlog() throws -> Data {
    let bundle = Bundle(for: type(of: self))
    // The file is "2026-08-04--03-38-17.qlog.zst"
    guard let url = bundle.url(forResource: "2026-08-04--03-38-17.qlog", withExtension: "zst") else {
      throw NSError(domain: "Test", code: 1, userInfo: [NSLocalizedDescriptionKey: "qlog file not found in test bundle"])
    }
    return try Data(contentsOf: url)
  }

  func testDecompressRealQlog() throws {
    let compressed = try loadRealQlog()
    XCTAssertEqual(compressed.count, 8912033, "Real qlog should be 8.9MB")

    let decompressed = QlogParser.decompressZstd(compressed)
    XCTAssertNotNil(decompressed, "Should decompress zstd")
    XCTAssertGreaterThan(decompressed!.count, 100000, "Decompressed should be > 100KB")
  }

  func testParseRealQlog() throws {
    let compressed = try loadRealQlog()
    let data = QlogParser.parse(compressed)

    // The real qlog has 10941 controlsState events
    XCTAssertEqual(data.t.count, 10941, "Should parse exactly 10941 controlsState events")
    XCTAssertEqual(data.t.count, data.c.count, "All arrays must be equal length")
    XCTAssertEqual(data.t.count, data.v.count)
    XCTAssertEqual(data.t.count, data.p.count)
  }

  func testParseSpeedData() throws {
    let compressed = try loadRealQlog()
    let data = QlogParser.parse(compressed)

    let maxV = data.v.max() ?? 0
    XCTAssertGreaterThan(maxV, 10.0, "Max speed should be > 10 m/s (real driving)")
  }

  func testParseTimeData() throws {
    let compressed = try loadRealQlog()
    let data = QlogParser.parse(compressed)

    XCTAssertGreaterThan(data.t.count, 100)
    // Time should be increasing
    for i in 1..<min(50, data.t.count) {
      XCTAssertGreaterThan(data.t[i], data.t[i-1], "Time should increase at \(i)")
    }
  }
}
