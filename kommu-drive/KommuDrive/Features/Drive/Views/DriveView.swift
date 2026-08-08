import SwiftUI

/// Main driving screen: SceneKit road view + HUD overlay.
/// The tab bar lives in RootView (always visible, even before BLE connects).
struct DriveView: View {
  @ObservedObject var viewModel: DriveSessionViewModel

  /// Tab identifiers — shared with RootView which owns the tab bar.
  enum Tab: String, CaseIterable { case drive, tuning, drives, settings }

  // The renderer is created once and shared between this view and the SceneView.
  @State private var renderer = DriveSceneRenderer()

  var body: some View {
    ZStack {
      // SceneKit road view
      DriveSceneView(viewModel: viewModel, renderer: renderer)
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .ignoresSafeArea()

      // HUD overlay
      driveHUD
    }
    .onChange(of: viewModel.connectionState) { _, state in
      if state.isConnected {
        viewModel.requestSettings()
      }
    }
    .onAppear {
      if viewModel.connectionState.isConnected {
        viewModel.requestSettings()
      }
    }
  }

  // MARK: Drive HUD (speed, engagement, confidence ball)

  private var driveHUD: some View {
    GeometryReader { geo in
      ZStack {
        // Top bar: speed + engagement + target speed
        VStack(spacing: 0) {
          topBar
          Spacer()
        }
        .padding(.horizontal, 8)
        .padding(.top, 2)

        // Confidence ball at bottom-left
        VStack {
          Spacer()
          HStack {
            ConfidenceBall(
              smoothedConfidence: viewModel.smoothedConfidence,
              engaged: viewModel.latestFrame?.enabled ?? false,
              trackHeight: min(geo.size.height * 0.3, 140),
              radius: 14
            )
            .padding(.leading, 6)
            .padding(.bottom, 6)
            Spacer()
          }
        }
      }
    }
  }

  // MARK: Bottom tab bar (static — used by RootView)

  static func tabBar(selectedTab: Binding<Tab>) -> some View {
    HStack(spacing: 0) {
      tabItem(icon: "car.fill", label: "Drive", tab: .drive, selectedTab: selectedTab)
      tabItem(icon: "slider.horizontal.3", label: "Tuning", tab: .tuning, selectedTab: selectedTab)
      tabItem(icon: "chart.line.uptrend.xyaxis", label: "Drives", tab: .drives, selectedTab: selectedTab)
      tabItem(icon: "gearshape.fill", label: "Settings", tab: .settings, selectedTab: selectedTab)
    }
    .padding(.top, 6)
    .padding(.bottom, 2)
    .background(Color(.systemBackground).opacity(0.95))
  }

  private static func tabItem(icon: String, label: String, tab: Tab, selectedTab: Binding<Tab>) -> some View {
    Button {
      withAnimation(.easeInOut(duration: 0.2)) {
        selectedTab.wrappedValue = tab
      }
    } label: {
      VStack(spacing: 2) {
        Image(systemName: icon)
          .font(.system(size: 18, weight: .semibold))
        Text(label)
          .font(.system(size: 10, weight: .medium))
      }
      .foregroundStyle(selectedTab.wrappedValue == tab ? Color.accentColor : .secondary)
      .frame(maxWidth: .infinity)
      .padding(.vertical, 6)
    }
    .buttonStyle(.plain)
  }

  // MARK: HUD — Top bar

  @ViewBuilder
  private var topBar: some View {
    let frame = viewModel.latestFrame
    let isMetric = frame?.isMetric ?? viewModel.settings.isMetric
    let speed = formatSpeed(frame?.vEgoCluster ?? 0, isMetric: isMetric)
    // Show the MODEL's desired speed (longitudinalPlan.speeds[0]), not the
    // cruise control set speed (that's already on the car's dashboard).
    let desired = frame?.desiredSpeed
    let target = desired != nil ? formatSpeed(desired!, isMetric: isMetric) : "—"
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

      // Right: target speed (the tab bar at the bottom handles navigation)
      VStack(alignment: .trailing, spacing: 4) {
        speedBadge(value: target, unit: speedUnit, label: "MODEL", accent: true)
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

  // MARK: Helpers

  private func formatSpeed(_ mps: Double, isMetric: Bool) -> String {
    let value = isMetric ? mps * 3.6 : mps * 2.23693629
    return String(format: "%.0f", value)
  }
}
