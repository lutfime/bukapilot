//
//  DriveChartsViewModelTests.swift
//  Tests the chart ViewModel: load, downsample, ChartPoint generation
//

import XCTest
@testable import KommuDrive

final class DriveChartsViewModelTests: XCTestCase {

  /// Loads the real qlog file and parses it into DriveData
  private func loadRealDriveData() throws -> DriveData {
    let bundle = Bundle(for: type(of: self))
    guard let url = bundle.url(forResource: "2026-08-04--03-38-17.qlog", withExtension: "zst") else {
      throw NSError(domain: "Test", code: 1, userInfo: [NSLocalizedDescriptionKey: "qlog file not found"])
    }
    let compressed = try Data(contentsOf: url)
    return QlogParser.parse(compressed)
  }

  @MainActor
  func testLoadPopulatesChartData() async throws {
    let data = try loadRealDriveData()
    let vm = DriveChartsViewModel(route: DriveRouteVM(rawRoute: "2026-08-04--03-38-17"))

    XCTAssertFalse(vm.isLoaded, "Should start unloaded")
    XCTAssertTrue(vm.steerData.isEmpty, "steerData should be empty before load")

    await vm.load(data: data)

    XCTAssertTrue(vm.isLoaded, "Should be loaded after load()")
    XCTAssertGreaterThan(vm.steerData.count, 50, "steerData should have downsampled points, got \(vm.steerData.count)")
    XCTAssertGreaterThan(vm.speedData.count, 50, "speedData should have points")
    XCTAssertGreaterThan(vm.pidP.count, 50, "pidP should have points")
    XCTAssertGreaterThan(vm.curveDesired.count, 50, "curveDesired should have points")
  }

  @MainActor
  func testSpeedChartPointsHaveRealData() async throws {
    let data = try loadRealDriveData()
    let vm = DriveChartsViewModel(route: DriveRouteVM(rawRoute: "2026-08-04--03-38-17"))
    await vm.load(data: data)

    // Speed points should have non-zero Y values (car was moving)
    let nonzeroSpeed = vm.speedData.filter { $0.y > 0 }
    XCTAssertGreaterThan(nonzeroSpeed.count, 20, "Should have non-zero speed points, got \(nonzeroSpeed.count)")

    // Check the max speed is reasonable (23 m/s = 83 km/h)
    let maxSpeed = vm.speedData.map(\.y).max() ?? 0
    XCTAssertGreaterThan(maxSpeed, 50, "Max speed should be > 50 km/h, got \(maxSpeed)")
  }

  @MainActor
  func testPointsHaveUniqueIDs() async throws {
    let data = try loadRealDriveData()
    let vm = DriveChartsViewModel(route: DriveRouteVM(rawRoute: "2026-08-04--03-38-17"))
    await vm.load(data: data)

    let ids = Set(vm.steerData.map(\.id))
    XCTAssertEqual(ids.count, vm.steerData.count, "All ChartPoint IDs should be unique")
  }

  @MainActor
  func testSummaryStats() async throws {
    let data = try loadRealDriveData()
    let vm = DriveChartsViewModel(route: DriveRouteVM(rawRoute: "2026-08-04--03-38-17"))
    await vm.load(data: data)

    XCTAssertFalse(vm.durationString.isEmpty, "Duration should not be empty")
    XCTAssertFalse(vm.avgSpeedString.isEmpty)
    XCTAssertFalse(vm.tuningGuidance.isEmpty)
    XCTAssertGreaterThan(vm.maxStep, -1, "Max step should be >= -1")
  }

  @MainActor
  func testWallClockToggle() async throws {
    // Parse with routeStartTime=0 (elapsed mode) — this is what the test qlog produces
    let data = try loadRealDriveData()
    let vm = DriveChartsViewModel(route: DriveRouteVM(rawRoute: "2026-08-04--03-38-17"))
    await vm.load(data: data)

    // Data parsed with routeStartTime=0, so timestamps are elapsed (small values)
    // Wall-clock mode just uses these directly since routeStartTime was 0
    let firstX = vm.steerData[0].x
    XCTAssertGreaterThanOrEqual(firstX, 0, "X should be >= 0")

    // Toggle to elapsed — should subtract first value
    vm.showWallClock = false
    let elapsedX = vm.steerData[0].x
    XCTAssertEqual(elapsedX, 0, accuracy: 0.1, "Elapsed X should start at 0")
  }
}
