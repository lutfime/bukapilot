import SwiftUI

/// Main driving screen: SceneKit road view (bottom layer) + SwiftUI HUD overlay.
///
/// The two layers are independent — SceneKit owns its 3D world coordinate space,
/// SwiftUI owns its screen-space HUD. They compose in a ZStack but don't
/// reference each other's positions, so there's no layout drift.
struct DriveView: View {
  @ObservedObject var viewModel: DriveSessionViewModel
  @State private var showSettings = false

  // The renderer is created once and shared between this view and the SceneView.
  @State private var renderer = DriveSceneRenderer()

  var body: some View {
    GeometryReader { geo in
      ZStack {
        // Layer 1: SceneKit road view (fills the whole screen)
        DriveSceneView(viewModel: viewModel, renderer: renderer)
          .frame(maxWidth: .infinity, maxHeight: .infinity)
          .ignoresSafeArea()

        // Layer 2: SwiftUI HUD overlay — top info bar + bottom controls
        VStack(spacing: 0) {
          topBar
          Spacer()

          // Bottom Bar: Driver Monitoring (Left), Steering Arc (Center), Confidence Dot (Right)
          HStack(alignment: .center, spacing: 0) {
            // Left: Comma 4 Steering Wheel / Driver Monitoring Badge
            driverMonitoringBadge
              .padding(.leading, 8)

            Spacer()

            // Center: Steering Limit Arc
            SteeringLimitArc(
              value: viewModel.smoothedSteeringLimit,
              engaged: viewModel.latestFrame?.enabled ?? false
            )
            .frame(width: 180, height: 75)

            Spacer()

            // Right: Comma 4 Confidence Status Ball
            ConfidenceBall(
              smoothedConfidence: viewModel.smoothedConfidence,
              engaged: viewModel.latestFrame?.enabled ?? false,
              trackHeight: 60,
              radius: 10
            )
            .padding(.trailing, 8)
          }
          .padding(.bottom, 12)
        }
        .padding(.horizontal, 8)
        .padding(.top, 2)
      }
    }
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

  // MARK: HUD — Top bar

  @ViewBuilder
  private var topBar: some View {
    let frame = viewModel.latestFrame
    let isMetric = frame?.isMetric ?? viewModel.settings.isMetric
    let speed = formatSpeed(frame?.vEgoCluster ?? 0, isMetric: isMetric)
    let target = formatSpeed(frame?.vCruiseCluster ?? 0, isMetric: isMetric)
    let speedUnit = isMetric ? "km/h" : "mph"

    return HStack(alignment: .top, spacing: 8) {
      // Left: current speed + lead distance
      VStack(alignment: .leading, spacing: 4) {
        speedBadge(value: speed, unit: speedUnit, label: "CURRENT")
        leadBadge(lead: frame?.leadOne)
      }

      Spacer()

      // Center: engagement status
      engagementBadge(frame: frame)

      Spacer()

      // Right: target speed + gear button
      VStack(alignment: .trailing, spacing: 4) {
        HStack(alignment: .top, spacing: 8) {
          speedBadge(value: target, unit: speedUnit, label: "TARGET", accent: true)
          Button {
            showSettings = true
          } label: {
            Image(systemName: "gearshape.fill")
              .font(.system(size: 16))
              .foregroundStyle(.secondary)
              .frame(width: 32, height: 32)
              .background(Color.white.opacity(0.08), in: Circle())
          }
        }
        // FPS counter
        Text(String(format: "%.1f fps · %d", viewModel.fps, viewModel.framesReceived))
          .font(.system(size: 10, design: .monospaced))
          .foregroundStyle(.tertiary)
      }
    }
  }

  // MARK: HUD pieces

  private func speedBadge(value: String, unit: String, label: String, accent: Bool = false) -> some View {
    VStack(alignment: .leading, spacing: 1) {
      Text(label)
        .font(.system(size: 9, weight: .semibold))
        .foregroundStyle(.secondary)
      HStack(alignment: .firstTextBaseline, spacing: 3) {
        Text(value)
          .font(.system(size: 32, weight: .bold, design: .rounded))
          .foregroundStyle(accent ? Color.accentColor : .primary)
        Text(unit)
          .font(.system(size: 12, weight: .medium))
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
      .font(.system(size: 11, weight: .heavy, design: .rounded))
      .padding(.horizontal, 10).padding(.vertical, 5)
      .background(color.opacity(0.18), in: Capsule())
      .overlay(Capsule().stroke(color, lineWidth: 1.5))
      .foregroundStyle(color)
  }

  private func leadBadge(lead: LeadData?) -> some View {
    let hasLead = lead?.hasLead ?? false
    let distance = lead?.distance ?? 0
    return HStack(alignment: .firstTextBaseline, spacing: 4) {
      Image(systemName: hasLead ? "car.fill" : "car")
        .font(.system(size: 11))
        .foregroundStyle(hasLead ? Color.cyan : .secondary)
      Text(hasLead ? String(format: "%.1f m", distance) : "no lead")
        .font(.system(size: 13, weight: .semibold, design: .rounded))
        .foregroundStyle(hasLead ? .primary : .secondary)
    }
  }

  /// Comma 4 Steering Wheel / Driver Monitoring Badge (Bottom Left)
  private var driverMonitoringBadge: some View {
    let enabled = viewModel.latestFrame?.enabled ?? false
    let ringColor: Color = enabled ? Color(red: 0/255, green: 235/255, blue: 130/255) : Color.white.opacity(0.3)
    return ZStack {
      Circle()
        .fill(Color.black.opacity(0.55))
        .frame(width: 38, height: 38)
      Circle()
        .stroke(ringColor, lineWidth: 2.5)
        .frame(width: 38, height: 38)
      Image(systemName: "steeringwheel")
        .font(.system(size: 19, weight: .bold))
        .foregroundStyle(enabled ? .white : .secondary)
    }
  }

  // MARK: Helpers

  private func formatSpeed(_ mps: Double, isMetric: Bool) -> String {
    let value = isMetric ? mps * 3.6 : mps * 2.23693629
    return String(format: "%.0f", value)
  }
}
