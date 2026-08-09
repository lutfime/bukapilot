//
//  KommuDriveTests.swift
//  Tests the full pipeline: device JSON output → DriveData decode → downsample → ChartPoints
//  Uses real data from 2026-08-04--03-38-17.qlog.zst (parsed by the same Python script the app uses on device)
//

import XCTest
@testable import KommuDrive

final class KommuDriveTests: XCTestCase {

  /// Loads the real parsed qlog output (what the device Python script produces).
  /// Source: result/drives/2026-08-04--03-38-17.qlog.zst parsed on-device.
  private func loadRealDriveData() throws -> DriveData {
    let bundle = Bundle(for: type(of: self))
    guard let url = bundle.url(forResource: "device_output", withExtension: "json") else {
      throw TestError.resourceNotFound("device_output.json")
    }
    let raw = try Data(contentsOf: url)
    return try JSONDecoder().decode(DriveData.self, from: raw)
  }

  enum TestError: Error {
    case resourceNotFound(String)
  }

  // MARK: - Real data: decode from device output

  func testDecodeRealQlogOutput() throws {
    let data = try loadRealDriveData()

    // Real qlog has 10941 controlsState events
    XCTAssertEqual(data.t.count, 10941, "Should have 10941 time points from real qlog")
    XCTAssertEqual(data.c.count, data.t.count, "c array must match t length")
    XCTAssertEqual(data.v.count, data.t.count, "v array must match t length")
    XCTAssertEqual(data.p.count, data.t.count, "p array must match t length")
    XCTAssertEqual(data.d.count, data.t.count, "d array must match t length")
    XCTAssertEqual(data.a.count, data.t.count, "a array must match t length")
    XCTAssertEqual(data.eng.count, data.t.count, "eng array must match t length")
    XCTAssertEqual(data.trq.count, data.t.count, "trq array must match t length")
  }

  // MARK: - Real data: speed values

  func testRealSpeedData() throws {
    let data = try loadRealDriveData()

    let maxV = data.v.max() ?? 0
    XCTAssertGreaterThan(maxV, 10.0, "Max speed should be > 10 m/s (real driving data)")

    let nonzeroV = data.v.filter { $0 > 0.1 }
    XCTAssertGreaterThan(nonzeroV.count, 5000, "Should have thousands of non-zero speed samples")
  }

  // MARK: - Real data: time is monotonic

  func testRealTimeIsMonotonic() throws {
    let data = try loadRealDriveData()

    for i in 1..<min(200, data.t.count) {
      XCTAssertGreaterThan(data.t[i], data.t[i-1], "Time must be increasing at \(i)")
    }

    let duration = (data.t.last ?? 0) - (data.t.first ?? 0)
    XCTAssertGreaterThan(duration, 100, "Drive duration should be > 100s")
  }

  // MARK: - Downsample with real data

  func testDownsampleRealData() throws {
    let data = try loadRealDriveData()
    let ds = data.downsampled(maxPoints: 100)

    XCTAssertEqual(ds.t.count, 100, "Should downsample 10941 → 100 points")
    XCTAssertEqual(ds.c.count, 100)
    XCTAssertEqual(ds.v.count, 100)
    XCTAssertEqual(ds.p.count, 100)

    // Downsampled time still increasing
    for i in 1..<ds.t.count {
      XCTAssertGreaterThan(ds.t[i], ds.t[i-1], "Downsampled time should increase at \(i)")
    }

    // Duration preserved
    let origDuration = (data.t.last ?? 0) - (data.t.first ?? 0)
    XCTAssertEqual(ds.duration, origDuration, accuracy: 1.0, "Duration preserved")
  }

  func testDownsampleMaxSpeed() throws {
    let data = try loadRealDriveData()
    let ds = data.downsampled(maxPoints: 100)

    let origMaxKmh = (data.v.max() ?? 0) * 3.6
    let dsMaxKmh = ds.maxSpeed
    XCTAssertEqual(dsMaxKmh, origMaxKmh, accuracy: 5.0, "Max speed roughly preserved after downsample")
  }

  // MARK: - ChartPoint generation (what the VM does)

  func testChartPointsFromRealData() throws {
    let data = try loadRealDriveData()
    let ds = data.downsampled(maxPoints: 100)

    // Simulate DriveChartsViewModel.rebuildXAxis()
    let x = ds.t  // wall-clock mode

    func makePoints(_ x: [Double], _ y: [Double]) -> [ChartPoint] {
      (0..<min(x.count, y.count)).map { ChartPoint(id: $0, x: x[$0], y: y[$0]) }
    }

    let steerPoints = makePoints(x, ds.c)
    let speedPoints = makePoints(x, ds.v.map { $0 * 3.6 })
    let pidP = makePoints(x, ds.p)
    let curveDesired = makePoints(x, ds.d)

    XCTAssertEqual(steerPoints.count, 100)
    XCTAssertEqual(speedPoints.count, 100)
    XCTAssertEqual(pidP.count, 100)
    XCTAssertEqual(curveDesired.count, 100)

    // Speed points should have non-zero Y (car was moving)
    let nonzeroSpeed = speedPoints.filter { $0.y > 0 }
    XCTAssertGreaterThan(nonzeroSpeed.count, 50, "Most speed points should be > 0")

    // Points have unique IDs
    let ids = Set(steerPoints.map { $0.id })
    XCTAssertEqual(ids.count, 100, "All IDs should be unique")

    // X values are wall-clock (not 0-based)
    XCTAssertGreaterThan(steerPoints[0].x, 0, "First X should be > 0 (wall clock)")
  }

  // MARK: - Summary stats from real data

  func testRealSummaryStats() throws {
    let data = try loadRealDriveData()
    let ds = data.downsampled(maxPoints: 100)

    XCTAssertGreaterThan(ds.duration, 100, "Duration > 100s")
    XCTAssertGreaterThan(ds.maxSpeed, 30, "Max speed > 30 km/h")
    XCTAssertGreaterThanOrEqual(ds.avgStep, 0, "Avg step >= 0")
    XCTAssertFalse(ds.tuningGuidance.isEmpty, "Should have tuning guidance")
    XCTAssertFalse(ds.durationString.isEmpty)
    XCTAssertFalse(ds.avgSpeedString.isEmpty)
  }

  // MARK: - DriveRouteVM

  func testDriveRouteVM() {
    let vm = DriveRouteVM(rawRoute: "2026-08-08--03-55-22--0", segmentCount: 3)

    XCTAssertEqual(vm.rawValue, "2026-08-08--03-55-22")
    XCTAssertEqual(vm.segmentCount, 3)
    XCTAssertTrue(vm.hasMultipleSegments)
    XCTAssertNotEqual(vm.startDate, Date(timeIntervalSince1970: 0))
  }

  func testDriveRouteVMSingleSegment() {
    let vm = DriveRouteVM(rawRoute: "2026-08-08--03-55-22")

    XCTAssertEqual(vm.rawValue, "2026-08-08--03-55-22")
    XCTAssertFalse(vm.hasMultipleSegments)
  }

  func testDriveRouteVMDateLabel() {
    let vm = DriveRouteVM(rawRoute: "2026-08-08--03-55-22")
    XCTAssertTrue(vm.dateLabel.contains("2026"))
    XCTAssertFalse(vm.dateLabel.isEmpty)
  }

  // MARK: - Edge cases

  func testDecodeEmptyArrays() {
    let json = #"{"t":[],"v":[],"c":[],"d":[],"a":[],"o":[],"p":[],"i":[],"f":[],"sat":[],"eng":[],"trq":[]}"#
    let data = json.data(using: .utf8)!
    let drive = try? JSONDecoder().decode(DriveData.self, from: data)

    XCTAssertNotNil(drive)
    XCTAssertTrue(drive!.isEmpty)
  }

  func testDecodeIntZerosAsDoubles() {
    // Real data has int 0 for PID output when not engaged
    let json = #"{"t":[1.0,2.0],"v":[0.0,10.0],"c":[0,0],"d":[0,0],"a":[0,0],"o":[0,0],"p":[0,0],"i":[0,0],"f":[0,0],"sat":[0,0],"eng":[0,0],"trq":[0.0,1.0]}"#
    let data = json.data(using: .utf8)!
    let drive = try? JSONDecoder().decode(DriveData.self, from: data)

    XCTAssertNotNil(drive, "Should decode int zeros into Double arrays")
    XCTAssertEqual(drive?.c, [0.0, 0.0])
    XCTAssertEqual(drive?.v, [0.0, 10.0])
  }

  func testDownsampleFewerThanMax() {
    let data = DriveData(
      t: [1.0, 2.0, 3.0], v: [10.0, 20.0, 30.0], c: [0.1, 0.2, 0.3],
      d: [0.001], a: [0.001], o: [0], p: [0.01], i: [0.005], f: [0.001],
      sat: [0], eng: [1], trq: [0.0]
    )
    let ds = data.downsampled(maxPoints: 100)
    XCTAssertEqual(ds.t.count, 3, "Should return all 3 points when < maxPoints")
  }
}
