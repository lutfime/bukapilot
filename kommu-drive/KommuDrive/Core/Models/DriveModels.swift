import Foundation

/// One lead car from `radarState`.
/// Fields mirror what `appbridged.py:extract_lead` ships: status / dRel / yRel.
/// `dRel` is meters from the front bumper; `yRel` is lateral meters.
struct LeadData: Equatable {
  /// 0 = no lead, 1 = lead detected (matches `status` in RadarPoint).
  let status: Int
  /// Distance in meters from front bumper (`dRel`).
  let distance: Double
  /// Lateral offset in meters (`yRel`).
  let yRel: Double

  var hasLead: Bool { status > 0 }

  /// Decode from a raw `[String: Any]` msgpack dict.
  /// Keys on the wire are single letters (s / d / y).
  init?(dict: [String: Any]) {
    guard let status = (dict["s"] as? Int) ?? (dict["s"] as? Double).map(Int.init) else { return nil }
    let d: Double = {
      if let n = dict["d"] as? Double { return n }
      if let n = dict["d"] as? Int { return Double(n) }
      return 0
    }()
    let y: Double = {
      if let n = dict["y"] as? Double { return n }
      if let n = dict["y"] as? Int { return Double(n) }
      return 0
    }()
    self.status = status
    self.distance = d
    self.yRel = y
  }

  init(status: Int, distance: Double, yRel: Double) {
    self.status = status
    self.distance = distance
    self.yRel = yRel
  }
}

/// A point sample along the predicted path / lane lines / road edges.
/// On the wire these arrive as already-resampled arrays of 1..8 doubles.
struct PathData: Equatable {
  let x: [Double]   // meters forward
  let y: [Double]   // meters lateral
}

/// Composite driving frame, decoded from a `CHANNEL_VISUALISATION` msgpack blob.
/// Keys mirror `appbridged.py:send_visualisation_message`.
struct DriveFrame: Equatable {
  let frameId: Int
  /// Predicted path (resampled). key "p"
  let path: PathData?
  /// Longitudinal acceleration profile. key "a"
  let acceleration: [Double]?
  /// Lane lines l1..l4, road edges r1..r2. keys "l1".."l4","r1","r2"
  let laneLines: [PathData]
  let roadEdges: [PathData]
  /// Lead cars. key "o" = leadOne, "t" = leadTwo
  let leadOne: LeadData?
  let leadTwo: LeadData?
  /// Cluster speeds from carState.
  let vEgoCluster: Double?     // current displayed speed, m/s
  let vCruiseCluster: Double?  // target/set speed, m/s
  /// openpilot engagement.
  let enabled: Bool
  let experimentalMode: Bool
  let state: Int               // SelfdriveState.OpenpilotState raw
  /// Alert text.
  let alertText1: String?
  let alertText2: String?
  let alertStatus: Int?
  let personality: Int?
  /// true = metric units requested by device.
  let isMetric: Bool
  let dongleId: String?

  /// Confidence-ball value in [0, 1]. key "cf".
  /// `(1 - max(brakeDisengageProbs)) * (1 - max(steerOverrideProbs))` from modelV2.meta.
  /// nil when the field is absent (older appbridged builds).
  let confidence: Double?

  /// Steering-limit fraction in [-1, 1]. key "sl".
  /// Lateral-accel-based; sign = direction of torque demand. nil when absent.
  let steeringLimit: Double?

  /// True ego speed in m/s (key "vEgo"), needed to recompute steering math on
  /// the phone if we ever extend it. May differ slightly from vEgoCluster.
  let vEgo: Double?

  /// All cars detected by the model (leadsV3), not just the radar lead.
  /// Each entry: {x: meters ahead, y: meters left, p: probability, v: speed m/s}.
  let detectedCars: [DetectedCar]

  /// Lane line probabilities (0..1) for l1..l4. Keys p1..p4.
  let laneLineProbs: [Double]
  /// Road edge stds for r1, r2. Keys s1, s2.
  let roadEdgeStds: [Double]
}

/// One car detected by the vision model (from modelV2.leadsV3).
struct DetectedCar: Equatable {
  let x: Double        // meters ahead
  let y: Double        // meters left (openpilot convention)
  let probability: Double
  let speed: Double    // absolute speed m/s
}

/// Decodes a raw msgpack visualisation dict into a `DriveFrame`.
/// The decoder is intentionally permissive: missing/short arrays default to empty/nil
/// so partial frames never crash the UI.
enum DriveFrameDecoder {
  static func decode(_ raw: [String: Any]) -> DriveFrame {
    func path(_ key: String) -> PathData? {
      // Path dicts on the wire can be either {"x":[...],"y":[...],"z":[...]}
      // (from `resample`) or a bare [Double] (from `extract_model_data` accel).
      if let dict = raw[key] as? [String: Any] {
        let x = (dict["x"] as? [Double]) ?? (dict["x"] as? [Int])?.map(Double.init) ?? []
        let y = (dict["y"] as? [Double]) ?? (dict["y"] as? [Int])?.map(Double.init) ?? []
        return PathData(x: x, y: y)
      }
      return nil
    }

    func lead(_ key: String) -> LeadData? {
      guard let dict = raw[key] as? [String: Any] else { return nil }
      return LeadData(dict: dict)
    }

    func doubleField(_ key: String) -> Double? {
      if let n = raw[key] as? Double { return n }
      if let n = raw[key] as? Int { return Double(n) }
      return nil
    }

    func intField(_ key: String) -> Int? {
      if let n = raw[key] as? Int { return n }
      if let n = raw[key] as? Double { return Int(n) }
      return nil
    }

    func boolField(_ key: String) -> Bool {
      (raw[key] as? Bool) ?? ((raw[key] as? Int).map { $0 != 0 }) ?? false
    }

    func stringField(_ key: String) -> String? {
      if let s = raw[key] as? String { return s }
      return nil
    }

    let laneKeys = ["l1", "l2", "l3", "l4"]
    let edgeKeys = ["r1", "r2"]

    let detectedCars: [DetectedCar] = {
      guard let arr = raw["cars"] as? [Any] else { return [] }
      return arr.compactMap { item -> DetectedCar? in
        guard let d = item as? [String: Any] else { return nil }
        func dbl(_ k: String) -> Double {
          if let n = d[k] as? Double { return n }
          if let n = d[k] as? Int { return Double(n) }
          return 0
        }
        return DetectedCar(x: dbl("x"), y: dbl("y"), probability: dbl("p"), speed: dbl("v"))
      }
    }()

    let laneLineProbs = ["p1", "p2", "p3", "p4"].compactMap { doubleField($0) }
    let roadEdgeStds = ["s1", "s2"].compactMap { doubleField($0) }

    return DriveFrame(
      frameId: intField("f") ?? 0,
      path: path("p"),
      acceleration: raw["a"] as? [Double],
      laneLines: laneKeys.compactMap { path($0) },
      roadEdges: edgeKeys.compactMap { path($0) },
      leadOne: lead("o"),
      leadTwo: lead("t"),
      vEgoCluster: doubleField("vEgoCluster"),
      vCruiseCluster: doubleField("vCruiseCluster"),
      enabled: boolField("enabled"),
      experimentalMode: boolField("experimentalMode"),
      state: intField("state") ?? 0,
      alertText1: stringField("alertText1"),
      alertText2: stringField("alertText2"),
      alertStatus: intField("alertStatus"),
      personality: intField("personality"),
      isMetric: boolField("m"),
      dongleId: stringField("d"),
      confidence: doubleField("cf"),
      steeringLimit: doubleField("sl"),
      vEgo: doubleField("vEgo"),
      detectedCars: detectedCars,
      laneLineProbs: laneLineProbs,
      roadEdgeStds: roadEdgeStds
    )
  }
}
