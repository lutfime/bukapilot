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
  @State private var showSettings = false

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

        // Confidence ball pinned to bottom-right corner — keeps the route
        // centered and unobstructed in the upper/center area.
        ConfidenceBall(
          smoothedConfidence: viewModel.smoothedConfidence,
          engaged: viewModel.latestFrame?.enabled ?? false,
          trackHeight: min(geo.size.height * 0.38, 180)
        )
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .bottomTrailing)
        .padding(.trailing, 10)
        .padding(.bottom, geo.size.height * 0.20)

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
    .sheet(isPresented: $showSettings) {
      SettingsSheet(viewModel: viewModel)
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
      VStack(spacing: 8) {
        speedBadge(value: formatSpeed(target, isMetric: frame?.isMetric ?? viewModel.settings.isMetric),
                   unit: speedUnit,
                   label: "TARGET",
                   accent: true)
      }
      // Settings gear button
      Button {
        showSettings = true
      } label: {
        Image(systemName: "gearshape.fill")
          .font(.system(size: 18))
          .foregroundStyle(.secondary)
          .frame(width: 36, height: 36)
          .background(Color.white.opacity(0.08), in: Circle())
      }
      .offset(y: -8)
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
    let engaged = frame?.enabled ?? false

    drawSurface(context: context, proj: proj)

    // Lane lines + road edges are hidden when disengaged (matches comma master).
    if engaged {
      drawLaneLines(context: context, proj: proj, frame: frame)
    }

    // Predicted path — also hidden when disengaged.
    if engaged, let path = frame?.path {
      drawPathCorridor(context: context, proj: proj, path: path, frame: frame)
    }

    // Detected cars always visible (useful even when disengaged).
    for car in frame?.detectedCars ?? [] {
      drawDetectedCar(context: context, proj: proj, car: car)
    }
    // Lead car marker.
    if let lead = frame?.leadOne, lead.hasLead {
      drawLeadMarker(context: context, proj: proj, lead: lead)
    }
    // Ego car at the bottom.
    drawEgoCar(context: context, proj: proj, frame: frame)
  }

  /// Lane lines + road edges, matching comma's model_renderer.py:
  /// - Lane lines drawn as ribbons whose width scales with probability
  /// - Color: green when engaged, gray when disengaged
  /// - Inner lane lines (l2, l3) are wider than outer (l1, l4)
  /// - Road edges turn green when adjacent lane lines aren't confident
  private func drawLaneLines(context: GraphicsContext, proj: RoadProjection, frame: DriveFrame?) {
    let lanes = frame?.laneLines ?? []
    let edges = frame?.roadEdges ?? []
    let probs = frame?.laneLineProbs ?? []
    let edgeStds = frame?.roadEdgeStds ?? []

    // Lane lines — comma uses green (0,255,64) when engaged.
    let laneColor = Color(red: 0, green: 255/255, blue: 64/255)

    for (i, lane) in lanes.enumerated() {
      let prob = i < probs.count ? probs[i] : 0.5
      // Inner lines (index 1, 2) are wider — comma uses 0.16 vs 0.12 factor.
      let widthFactor: CGFloat = (i == 1 || i == 2) ? 0.16 : 0.12
      let alpha = min(max(prob, 0.0), 0.7)
      drawRibbon(context: context, proj: proj, path: lane,
                 widthFactor: widthFactor, color: laneColor.opacity(alpha))
    }

    // Road edges — green if adjacent lane line isn't confident (prob < 0.25),
    // otherwise white. Matches comma's _draw_lane_lines logic.
    for (i, edge) in edges.enumerated() {
      let edgeStd = i < edgeStds.count ? edgeStds[i] : 0.5
      let adjProb = (i + 1) < probs.count ? probs[i + 1] : 0.5
      let notConfident = adjProb < 0.25
      let alpha = min(max(1.0 - edgeStd, 0.0), 0.7)
      let color: Color = notConfident
        ? laneColor.opacity(alpha)
        : Color.white.opacity(alpha)
      drawRibbon(context: context, proj: proj, path: edge,
                 widthFactor: 0.12, color: color)
    }
  }

  /// Draw a path as a ribbon (polygon) — matches comma's _map_line_to_polygon.
  /// The ribbon width foreshortens toward the horizon.
  private func drawRibbon(context: GraphicsContext, proj: RoadProjection,
                          path: PathData, widthFactor: CGFloat, color: Color) {
    let pts = projectPoints(path, proj: proj)
    guard pts.count >= 2 else { return }

    var left = [CGPoint](); var right = [CGPoint]()
    for (i, p) in pts.enumerated() {
      let t = CGFloat(i) / CGFloat(Swift.max(pts.count - 1, 1))
      // Foreshorten: wider near, narrower far. Scale by widthFactor.
      let baseW = proj.pixelsPerMeterAtBottom * widthFactor * 2
      let hw = baseW * (1.0 - t * 0.82)
      left.append(CGPoint(x: p.x - hw, y: p.y))
      right.append(CGPoint(x: p.x + hw, y: p.y))
    }
    var ribbon = Path()
    ribbon.move(to: left[0])
    for p in left.dropFirst() { ribbon.addLine(to: p) }
    for p in right.reversed() { ribbon.addLine(to: p) }
    ribbon.closeSubpath()
    context.fill(ribbon, with: .color(color))
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

  /// Draw the predicted path as a filled corridor with a vertical gradient.
  /// Matches comma's model_renderer.py _draw_path:
  ///   - Green gradient when throttle is allowed (engaged, normal driving)
  ///   - White/gray gradient when no throttle (braking / approaching lead)
  ///   - Path narrows when a lead car is close
  private func drawPathCorridor(context: GraphicsContext, proj: RoadProjection,
                                path: PathData, frame: DriveFrame?) {
    let pts = projectPoints(path, proj: proj)
    guard pts.count >= 2 else { return }

    // Comma narrows the path when a lead is close. We approximate the same effect.
    var maxIdx = pts.count
    if let lead = frame?.leadOne, lead.hasLead {
      let leadD = lead.distance * 2.0
      let clipD = leadD - min(leadD * 0.35, 10.0)
      // Find how many points fall within the clip distance.
      // We don't have the raw x[] here (already projected), so approximate by index ratio.
      if clipD > 0 {
        maxIdx = max(2, Int(Double(pts.count) * min(1.0, clipD / 100.0)))
      }
    }
    let drawPts = Array(pts.prefix(maxIdx))
    guard drawPts.count >= 2 else { return }

    // Build the ribbon.
    let halfWidthNear: CGFloat = 16
    let halfWidthFar: CGFloat = 2
    var left = [CGPoint](); var right = [CGPoint]()
    for (i, p) in drawPts.enumerated() {
      let t = CGFloat(i) / CGFloat(Swift.max(drawPts.count - 1, 1))
      let hw = halfWidthNear + (halfWidthFar - halfWidthNear) * t
      left.append(CGPoint(x: p.x - hw, y: p.y))
      right.append(CGPoint(x: p.x + hw, y: p.y))
    }
    var ribbon = Path()
    ribbon.move(to: left[0])
    for p in left.dropFirst() { ribbon.addLine(to: p) }
    for p in right.reversed() { ribbon.addLine(to: p) }
    ribbon.closeSubpath()

    // Gradient: green (throttle ok) top→bottom. Comma uses:
    //   THROTTLE: (13,248,122,102) → (114,255,92,89) → (114,255,92,0)
    // We approximate with a vertical gradient on the ribbon bounding box.
    let bounds = ribbon.boundingRect
    let gradient = GraphicsContext.Shading.linearGradient(
      Gradient(colors: [
        Color(red: 114/255, green: 255/255, blue: 92/255).opacity(0.0),    // far (top, fading out)
        Color(red: 114/255, green: 255/255, blue: 92/255).opacity(0.35),   // mid
        Color(red: 13/255, green: 248/255, blue: 122/255).opacity(0.40),   // near (bottom, solid green)
      ]),
      startPoint: CGPoint(x: bounds.midX, y: bounds.minY),
      endPoint: CGPoint(x: bounds.midX, y: bounds.maxY)
    )
    context.fill(ribbon, with: gradient)
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

  /// Draw a model-detected car (from leadsV3). Dimmer and smaller than the
  /// radar lead — these are all the cars the vision model sees around you.
  /// Opacity scales with detection probability so faint detections fade out.
  private func drawDetectedCar(context: GraphicsContext, proj: RoadProjection, car: DetectedCar) {
    guard let pos = proj.project(xForward: car.x, yLeft: car.y) else { return }
    let alpha = min(1.0, max(0.15, car.probability))
    let rect = CGRect(x: pos.x - 7, y: pos.y - 4, width: 14, height: 8)
    let round = RoundedRectangle(cornerRadius: 2).path(in: rect)
    context.fill(round, with: .color(Color.cyan.opacity(0.45 * alpha)))
    context.stroke(round, with: .color(Color.cyan.opacity(0.5 * alpha)), lineWidth: 0.8)
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
