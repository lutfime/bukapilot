import Testing
import Foundation
@testable import KommuDrive

/// Tests that verify the drive data parsing pipeline works end-to-end.
/// Uses synthetic data that mirrors the real qlog parse output.
struct DriveDataParsingTests {

  // MARK: - DriveData decode

  @Test func testDecodeDriveData() throws {
    // Simulate what the device Python script outputs
    let json = """
    {
      "t": [1.0, 2.0, 3.0, 4.0, 5.0],
      "v": [10.0, 15.0, 20.0, 18.0, 12.0],
      "c": [0.1, -0.2, 0.3, 0.0, -0.1],
      "d": [0.001, 0.002, 0.003, 0.002, 0.001],
      "a": [0.0009, 0.0018, 0.0029, 0.0019, 0.0009],
      "o": [0, 0, 1, 0, 0],
      "p": [0.05, 0.1, 0.15, 0.08, 0.02],
      "i": [0.01, 0.02, 0.03, 0.02, 0.01],
      "f": [0.001, 0.002, 0.003, 0.002, 0.001],
      "sat": [0, 0, 1, 0, 0],
      "eng": [1, 1, 1, 1, 1],
      "trq": [0.0, 1.5, 2.0, -0.5, 0.0]
    }
    """

    let data = json.data(using: .utf8)!
    let drive = try JSONDecoder().decode(DriveData.self, from: data)

    #expect(drive.t.count == 5)
    #expect(drive.c.count == 5)
    #expect(drive.v.count == 5)
    #expect(drive.p.count == 5)
    #expect(drive.eng == [1, 1, 1, 1, 1])
    #expect(drive.v[0] == 10.0)
    #expect(drive.c[2] == 0.3)
    #expect(!drive.isEmpty)
  }

  // MARK: - Downsample

  @Test func testDownsampleManyPoints() {
    // Create 1190 points (matching real drive data)
    var t: [Double] = []; var v: [Double] = []; var c: [Double] = []
    var d: [Double] = []; var a: [Double] = []; var o: [Int] = []
    var p: [Double] = []; var i: [Double] = []; var f: [Double] = []
    var sat: [Int] = []; var eng: [Int] = []; var trq: [Double] = []

    for idx in 0..<1190 {
      t.append(Double(idx) * 0.1)
      v.append(Double(idx) * 0.1)
      c.append(sin(Double(idx) * 0.1))
      d.append(0.001)
      a.append(0.001)
      o.append(idx % 5 == 0 ? 1 : 0)
      p.append(0.01)
      i.append(0.005)
      f.append(0.001)
      sat.append(0)
      eng.append(1)
      trq.append(Double(idx) * 0.1)
    }

    let data = DriveData(t: t, v: v, c: c, d: d, a: a, o: o, p: p, i: i, f: f, sat: sat, eng: eng, trq: trq)
    let ds = data.downsampled(maxPoints: 100)

    #expect(ds.t.count == 100, "Downsample should produce 100 points, got \(ds.t.count)")
    #expect(ds.c.count == 100)
    #expect(ds.v.count == 100)
    #expect(ds.p.count == 100)
    #expect(ds.duration > 0)
    #expect(ds.avgStep >= 0)
  }

  @Test func testDownsampleFewPoints() {
    // Fewer than maxPoints — should return as-is
    let data = DriveData(
      t: [1.0, 2.0, 3.0],
      v: [10.0, 20.0, 30.0],
      c: [0.1, 0.2, 0.3],
      d: [0.001], a: [0.001], o: [0],
      p: [0.01], i: [0.005], f: [0.001],
      sat: [0], eng: [1], trq: [0.0]
    )
    let ds = data.downsampled(maxPoints: 100)
    #expect(ds.t.count == 3)
  }

  // MARK: - ChartPoint generation

  @Test func testChartPointGeneration() {
    let data = DriveData(
      t: [100.0, 200.0, 300.0],
      v: [10.0, 20.0, 30.0],
      c: [0.1, 0.2, 0.3],
      d: [0.001], a: [0.001], o: [0],
      p: [0.01], i: [0.005], f: [0.001],
      sat: [0], eng: [1], trq: [0.0]
    )

    let ds = data.downsampled(maxPoints: 100)
    let xWall = ds.t
    let first = ds.t.first ?? 0
    let xElapsed = ds.t.map { $0 - first }

    // Wall-clock points
    let wallPoints = (0..<min(xWall.count, ds.c.count)).map {
      ChartPoint(id: $0, x: xWall[$0], y: ds.c[$0])
    }
    #expect(wallPoints.count == 3)
    #expect(wallPoints[0].x == 100.0)
    #expect(wallPoints[0].y == 0.1)

    // Elapsed points
    let elapsedPoints = (0..<min(xElapsed.count, ds.c.count)).map {
      ChartPoint(id: $0, x: xElapsed[$0], y: ds.c[$0])
    }
    #expect(elapsedPoints.count == 3)
    #expect(elapsedPoints[0].x == 0.0)
  }

  // MARK: - DriveRouteVM

  @Test func testDriveRouteVM() {
    let vm = DriveRouteVM(rawRoute: "2026-08-08--03-55-22--0", segmentCount: 3)

    #expect(vm.rawValue == "2026-08-08--03-55-22")
    #expect(vm.segmentCount == 3)
    #expect(vm.hasMultipleSegments)
    #expect(vm.startDate != Date(timeIntervalSince1970: 0))
  }

  @Test func testDriveRouteVMSingleSegment() {
    let vm = DriveRouteVM(rawRoute: "2026-08-08--03-55-22")

    #expect(vm.rawValue == "2026-08-08--03-55-22")
    #expect(vm.segmentCount == 1)
    #expect(!vm.hasMultipleSegments)
  }
}
