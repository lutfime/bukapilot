import Foundation
import SwiftUI

/// A single chart point — precomputed so charts iterate structs, not array indices.
struct ChartPoint: Identifiable, Hashable {
  let id: Int
  let x: Double
  let y: Double
}

/// ViewModel for the drive charts view.
/// Empty init, then async load() sets all @Published data.
final class DriveChartsViewModel: ObservableObject {
  let route: DriveRouteVM

  @Published var isLoaded = false
  @Published var steerData: [ChartPoint] = []
  @Published var pidP: [ChartPoint] = []
  @Published var pidI: [ChartPoint] = []
  @Published var pidF: [ChartPoint] = []
  @Published var curveDesired: [ChartPoint] = []
  @Published var curveActual: [ChartPoint] = []
  @Published var speedData: [ChartPoint] = []

  @Published var showWallClock: Bool = true {
    didSet { if isLoaded { rebuildXAxis() } }
  }

  @Published var durationString = ""
  @Published var avgSpeedString = ""
  @Published var maxSpeedString = ""
  @Published var avgStep: Double = 0
  @Published var maxStep: Double = 0
  @Published var overrides: Int = 0
  @Published var tuningGuidance = ""
  @Published var qlogFiles: [URL] = []
  @Published var dateString = ""

  private var ds: DownsampledData?

  private static let dateFormatter: DateFormatter = {
    let f = DateFormatter()
    f.dateStyle = .medium
    f.timeStyle = .short
    return f
  }()

  init(route: DriveRouteVM) {
    self.route = route
  }

  @MainActor
  func load(data: DriveData) async {
    let downsampled = data.downsampled(maxPoints: 100)
    self.ds = downsampled
    rebuildXAxis()
    AppLog.info("chartsVM.load: \(data.t.count) raw → \(steerData.count) downsampled points")
    durationString = downsampled.durationString
    avgSpeedString = downsampled.avgSpeedString
    maxSpeedString = downsampled.maxSpeedString
    avgStep = downsampled.avgStep
    maxStep = downsampled.maxStep
    overrides = downsampled.overrides
    tuningGuidance = downsampled.tuningGuidance
    dateString = Self.dateFormatter.string(from: route.startDate)

    let dir = route.qlogDir
    qlogFiles = (try? FileManager.default.contentsOfDirectory(at: dir, includingPropertiesForKeys: nil)) ?? []

    isLoaded = true
  }

  private func rebuildXAxis() {
    guard let ds = ds else { return }
    let x: [Double]
    if showWallClock {
      x = ds.t
    } else {
      let first = ds.t.first ?? 0
      x = ds.t.map { $0 - first }
    }
    func makePoints(_ x: [Double], _ y: [Double]) -> [ChartPoint] {
      (0..<min(x.count, y.count)).map { ChartPoint(id: $0, x: x[$0], y: y[$0]) }
    }
    steerData = makePoints(x, ds.c)
    pidP = makePoints(x, ds.p)
    pidI = makePoints(x, ds.i)
    pidF = makePoints(x, ds.f)
    curveDesired = makePoints(x, ds.d)
    curveActual = makePoints(x, ds.a)
    speedData = makePoints(x, ds.v.map { $0 * 3.6 })
  }
}
