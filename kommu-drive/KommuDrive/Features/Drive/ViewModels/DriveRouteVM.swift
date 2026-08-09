import Foundation

/// ViewModel for a drive route. Holds the parsed route + precomputed display strings.
/// Used in route list rows, chart headers, etc.
struct DriveRouteVM: Hashable, Identifiable {
  let rawValue: String
  let startDate: Date
  let segmentCount: Int

  var id: String { rawValue }
  var hasMultipleSegments: Bool { segmentCount > 1 }

  private static let dateFormatter: DateFormatter = {
    let f = DateFormatter()
    f.dateStyle = .medium
    f.timeStyle = .short
    return f
  }()

  var dateLabel: String { Self.dateFormatter.string(from: startDate) }

  init(rawRoute: String, segmentCount: Int = 1) {
    // Strip the trailing --N segment suffix if present
    if let dash = rawRoute.range(of: "--", options: .backwards) {
      let suffix = rawRoute[dash.upperBound...]
      if suffix.allSatisfy(\.isNumber), !suffix.isEmpty {
        self.rawValue = String(rawRoute[..<dash.lowerBound])
      } else {
        self.rawValue = rawRoute
      }
    } else {
      self.rawValue = rawRoute
    }
    self.segmentCount = segmentCount

    // Parse "2026-08-08--03-55-22" → Date (UTC)
    let parts = self.rawValue.split(separator: "--")
    let dateStr = parts.count >= 2 ? "\(parts[0]) \(parts[1])" : self.rawValue
    let df = DateFormatter()
    df.dateFormat = "yyyy-MM-dd HH-mm-ss"
    df.timeZone = TimeZone(identifier: "UTC")
    self.startDate = df.date(from: dateStr) ?? Date()
  }

  /// The qlog directory in DriveCache for this route.
  var qlogDir: URL {
    DeviceService.cacheDir.appendingPathComponent(rawValue, isDirectory: true)
  }
}
