import SwiftUI

/// The main driving screen: a perspective-projected road rendered with Core
/// Graphics inside a SwiftUI `Canvas`, with a HUD overlay for speed/target
/// speed/lead distance.
///
/// Phase 1 scope:
///   - road surface + horizon
///   - lane lines (l1..l4) and road edges (r1, r2)
///   - predicted path (filled corridor)
///   - lead car marker (leadOne distance + lateral)
///   - ego car icon at the bottom
///   - HUD: current speed, target speed, lead distance, engagement state
struct DriveView: View {

  @ObservedObject var viewModel: DriveSessionViewModel

  var body: some View {
    GeometryReader { geo in
      ZStack {
        // Background gradient (sky → road)
        LinearGradient(
          colors: [Color(red: 0.06, green: 0.07, blue: 0.10),
                   Color(red: 0.10, green: 0.11, blue: 0.14)],
          startPoint: .top,
          endPoint: .bottom
        )
        .ignoresSafeArea()

        // Canvas draws every frame using the latest DriveFrame.
        Canvas { context, size in
          drawRoad(context: context, size: size)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)

        // Confidence ball pinned to the right edge (matches device side panel).
        ConfidenceBall(
          smoothedConfidence: viewModel.smoothedConfidence,
          engaged: viewModel.latestFrame?.enabled ?? false,
          trackHeight: min(geo.size.height * 0.55, 280)
        )
        .frame(maxWidth: .infinity, alignment: .trailing)
        .padding(.trailing, 8)
        .padding(.top, geo.size.height * 0.12)

        // HUD overlay
        VStack {
          topBar
          Spacer()
          // Steering-limit arc sits just above the ego car / bottom bar.
          SteeringLimitArc(
            value: viewModel.smoothedSteeringLimit,
            engaged: viewModel.latestFrame?.enabled ?? false
          )
          .frame(maxWidth: .infinity)
          bottomBar
        }
        .padding()
      }
    }
    // As soon as we're connected, make sure the device streams visualisation.
    .onChange(of: viewModel.connectionState) { _, state in
      if state.isConnected {
        viewModel.requestVisualisation()
      }
    }
    .onAppear {
      if viewModel.connectionState.isConnected {
        viewModel.requestVisualisation()
      }
    }
  }

  // MARK: HUD

  private var topBar: some View {
    let frame = viewModel.latestFrame
    let speed = frame?.vEgoCluster ?? 0
    let target = frame?.vCruiseCluster ?? 0
    let speedUnit = (frame?.isMetric ?? viewModel.settings.isMetric) ? "km/h" : "mph"
    return HStack(alignment: .top) {
      speedBadge(value: formatSpeed(speed, isMetric: frame?.isMetric ?? viewModel.settings.isMetric),
                 unit: speedUnit,
                 label: "CURRENT")
      Spacer()
      engagementBadge(frame: frame)
      Spacer()
      speedBadge(value: formatSpeed(target, isMetric: frame?.isMetric ?? viewModel.settings.isMetric),
                 unit: speedUnit,
                 label: "TARGET",
                 accent: true)
    }
  }

  private var bottomBar: some View {
    let frame = viewModel.latestFrame
    let lead = frame?.leadOne
    return HStack(alignment: .bottom) {
      leadBadge(lead: lead, isMetric: frame?.isMetric ?? viewModel.settings.isMetric)
      Spacer()
      VStack(alignment: .trailing, spacing: 4) {
        if let dongle = viewModel.settings.dongleID, !dongle.isEmpty {
          Text(dongle)
            .font(.system(.caption2, design: .monospaced))
            .foregroundStyle(.secondary)
        }
        Text(String(format: "%.1f fps · %d frames", viewModel.fps, viewModel.framesReceived))
          .font(.system(.caption2, design: .monospaced))
          .foregroundStyle(.tertiary)
      }
    }
  }

  // MARK: HUD pieces

  private func speedBadge(value: String, unit: String, label: String, accent: Bool = false) -> some View {
    VStack(alignment: .leading, spacing: 2) {
      Text(label)
        .font(.system(size: 10, weight: .semibold))
        .foregroundStyle(.secondary)
      HStack(alignment: .firstTextBaseline, spacing: 4) {
        Text(value)
          .font(.system(size: 38, weight: .bold, design: .rounded))
          .foregroundStyle(accent ? Color.accentColor : .primary)
        Text(unit)
          .font(.system(size: 14, weight: .medium))
          .foregroundStyle(.secondary)
      }
    }
  }

  private func engagementBadge(frame: DriveFrame?) -> some View {
    let enabled = frame?.enabled ?? false
    let experimental = frame?.experimentalMode ?? false
    let text = enabled ? (experimental ? "EXPERIMENTAL" : "ENGAGED") : "STANDBY"
    let color: Color = enabled ? (experimental ? .orange : .green) : .gray
    return Text(text)
      .font(.system(size: 12, weight: .heavy, design: .rounded))
      .padding(.horizontal, 12).padding(.vertical, 6)
      .background(color.opacity(0.18), in: Capsule())
      .overlay(Capsule().stroke(color, lineWidth: 1.5))
      .foregroundStyle(color)
  }

  private func leadBadge(lead: LeadData?, isMetric: Bool) -> some View {
    let hasLead = lead?.hasLead ?? false
    let distance = lead?.distance ?? 0
    let unit = "m"
    return VStack(alignment: .leading, spacing: 2) {
      Text("LEAD CAR")
        .font(.system(size: 10, weight: .semibold))
        .foregroundStyle(.secondary)
      HStack(alignment: .firstTextBaseline, spacing: 4) {
        Image(systemName: hasLead ? "car.fill" : "car")
          .foregroundStyle(hasLead ? Color.cyan : .secondary)
        Text(hasLead ? String(format: "%.1f %@", distance, unit) : "—")
          .font(.system(size: 18, weight: .semibold, design: .rounded))
          .foregroundStyle(hasLead ? .primary : .secondary)
      }
    }
  }

  // MARK: Canvas drawing

  private func drawRoad(context: GraphicsContext, size: CGSize) {
    let proj = RoadProjection(size: size)
    let frame = viewModel.latestFrame

    drawSurface(context: context, proj: proj)

    // Road edges first (underneath).
    for edge in frame?.roadEdges ?? [] {
      drawPathLine(context: context, proj: proj, path: edge, color: .gray.opacity(0.6), lineWidth: 1.5, dashed: true)
    }
    // Lane lines.
    for lane in frame?.laneLines ?? [] {
      drawPathLine(context: context, proj: proj, path: lane, color: .white.opacity(0.85), lineWidth: 2)
    }
    // Predicted path (filled corridor) on top.
    if let path = frame?.path {
      drawPathCorridor(context: context, proj: proj, path: path)
    }
    // Lead car marker.
    if let lead = frame?.leadOne, lead.hasLead {
      drawLeadMarker(context: context, proj: proj, lead: lead)
    }
    // Ego car at the bottom.
    drawEgoCar(context: context, proj: proj, frame: frame)
  }

  private func drawSurface(context: GraphicsContext, proj: RoadProjection) {
    // Subtle road wedge from bottom toward the horizon.
    var path = Path()
    let leftBottom = CGPoint(x: proj.size.width * 0.5 - proj.pixelsPerMeterAtBottom * 2.5, y: proj.bottomY)
    let rightBottom = CGPoint(x: proj.size.width * 0.5 + proj.pixelsPerMeterAtBottom * 2.5, y: proj.bottomY)
    let horizonLeft = CGPoint(x: proj.size.width * 0.5 - 12, y: proj.horizonY)
    let horizonRight = CGPoint(x: proj.size.width * 0.5 + 12, y: proj.horizonY)
    path.move(to: leftBottom)
    path.addLine(to: horizonLeft)
    path.addLine(to: horizonRight)
    path.addLine(to: rightBottom)
    path.closeSubpath()
    context.fill(path, with: .color(.white.opacity(0.03)))

    // Horizon line.
    var horizon = Path()
    horizon.move(to: CGPoint(x: 0, y: proj.horizonY))
    horizon.addLine(to: CGPoint(x: proj.size.width, y: proj.horizonY))
    context.stroke(horizon, with: .color(.white.opacity(0.08)), lineWidth: 1)
  }

  private func projectPoints(_ data: PathData, proj: RoadProjection) -> [CGPoint] {
    // x[] and y[] may have different lengths after resampling; zip by index.
    let count = Swift.min(data.x.count, data.y.count)
    var pts: [CGPoint] = []
    pts.reserveCapacity(count)
    for i in 0..<count {
      if let p = proj.project(xForward: data.x[i], yLeft: data.y[i]) {
        pts.append(p)
      }
    }
    return pts
  }

  private func drawPathLine(context: GraphicsContext, proj: RoadProjection,
                            path: PathData, color: Color, lineWidth: CGFloat, dashed: Bool = false) {
    let pts = projectPoints(path, proj: proj)
    guard pts.count >= 2 else { return }
    var p = Path()
    p.move(to: pts[0])
    for i in 1..<pts.count { p.addLine(to: pts[i]) }
    if dashed {
      context.stroke(p, with: .color(color), style: StrokeStyle(lineWidth: lineWidth, dash: [6, 6]))
    } else {
      context.stroke(p, with: .color(color), lineWidth: lineWidth)
    }
  }

  private func drawPathCorridor(context: GraphicsContext, proj: RoadProjection, path: PathData) {
    let pts = projectPoints(path, proj: proj)
    guard pts.count >= 2 else { return }

    // Build a ribbon by offsetting the centerline laterally. We approximate the
    // corridor width as a constant in screen pixels that narrows toward horizon.
    let halfWidthNear: CGFloat = 18
    let halfWidthFar: CGFloat = 3
    var left = [CGPoint](); var right = [CGPoint]()
    for (i, p) in pts.enumerated() {
      let t = CGFloat(i) / CGFloat(Swift.max(pts.count - 1, 1))
      let hw = halfWidthNear + (halfWidthFar - halfWidthNear) * t
      left.append(CGPoint(x: p.x - hw, y: p.y))
      right.append(CGPoint(x: p.x + hw, y: p.y))
    }
    var ribbon = Path()
    ribbon.move(to: left[0])
    for p in left.dropFirst() { ribbon.addLine(to: p) }
    for p in right.reversed() { ribbon.addLine(to: p) }
    ribbon.closeSubpath()
    context.fill(ribbon, with: .color(Color.accentColor.opacity(0.22)))

    // Centerline on top.
    var center = Path()
    center.move(to: pts[0])
    for p in pts.dropFirst() { center.addLine(to: p) }
    context.stroke(center, with: .color(Color.accentColor), lineWidth: 2.5)
  }

  private func drawLeadMarker(context: GraphicsContext, proj: RoadProjection, lead: LeadData) {
    guard let pos = proj.project(xForward: lead.distance, yLeft: lead.yRel) else { return }
    let rect = CGRect(x: pos.x - 10, y: pos.y - 6, width: 20, height: 12, relativeTo: .zero)
    let round = RoundedRectangle(cornerRadius: 3).path(in: rect)
    context.fill(round, with: .color(Color.cyan.opacity(0.9)))
    context.stroke(round, with: .color(.white.opacity(0.6)), lineWidth: 1)

    // Distance line from ego car up to the lead.
    var line = Path()
    line.move(to: CGPoint(x: proj.size.width * 0.5, y: proj.bottomY - 8))
    line.addLine(to: pos)
    context.stroke(line, with: .color(Color.cyan.opacity(0.25)), style: StrokeStyle(lineWidth: 1, dash: [4, 6]))
  }

  private func drawEgoCar(context: GraphicsContext, proj: RoadProjection, frame: DriveFrame?) {
    let cx = proj.size.width * 0.5
    let cy = proj.bottomY + 6
    let enabled = frame?.enabled ?? false
    let color: Color = enabled ? .accentColor : .white.opacity(0.85)
    let body = CGRect(x: cx - 14, y: cy - 10, width: 28, height: 18)
    context.fill(
      RoundedRectangle(cornerRadius: 5).path(in: body),
      with: .color(color.opacity(0.9))
    )
    // Windshield hint
    let glass = CGRect(x: cx - 10, y: cy - 7, width: 20, height: 5)
    context.fill(
      RoundedRectangle(cornerRadius: 2).path(in: glass),
      with: .color(.black.opacity(0.25))
    )
  }

  // MARK: Helpers

  private func formatSpeed(_ mps: Double, isMetric: Bool) -> String {
    // vEgoCluster / vCruiseCluster from openpilot are in m/s.
    let value = isMetric ? mps * 3.6 : mps * 2.23693629
    return String(format: "%.0f", value)
  }
}

extension CGRect {
  init(x: CGFloat, y: CGFloat, width: CGFloat, height: CGFloat, relativeTo _: CGPoint) {
    self.init(x: x, y: y, width: width, height: height)
  }
}
