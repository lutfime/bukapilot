import SwiftUI
import Charts

/// Drive browser: lists drives on the device → tap one → see charts (steer, curvature, speed).
/// Uses DriveBrowserViewModel (which uses DeviceService via SSH) — full UI, SSH invisible.
struct DriveBrowserSheet: View {
  @ObservedObject var viewModel: DriveSessionViewModel
  @StateObject private var browserVM = DriveBrowserViewModel()

  var body: some View {
    NavigationStack {
      Group {
        if !browserVM.isConnected {
          connectingView
        } else if let route = browserVM.selectedRoute {
          if browserVM.isLoading {
            VStack(spacing: 16) {
              ProgressView()
              Text("Parsing qlog on device…")
                .font(.system(size: 13))
                .foregroundStyle(.secondary)
              Text("This reads the drive's qlog over SSH and extracts signals. May take a few seconds for long drives.")
                .font(.system(size: 11))
                .foregroundStyle(.tertiary)
                .multilineTextAlignment(.center)
                .padding(.horizontal, 32)
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)
          } else if let data = browserVM.driveData {
            DriveChartsView(data: data, route: route) {
              browserVM.selectedRoute = nil
            }
          } else if let err = browserVM.error {
            VStack(spacing: 12) {
              Image(systemName: "exclamationmark.triangle.fill").foregroundStyle(.orange)
              Text(err)
                .font(.system(size: 12, design: .monospaced))
                .foregroundStyle(.secondary)
                .multilineTextAlignment(.center)
                .textSelection(.enabled)
              Button("Copy error") {
                UIPasteboard.general.string = err
              }
              .buttonStyle(.bordered)
              .tint(.secondary)
              Button("Back to drives") {
                browserVM.selectedRoute = nil
                browserVM.error = nil
              }
              .buttonStyle(.bordered)
            }
            .padding()
          }
        } else {
          routeListView
        }
      }
      .navigationTitle(browserVM.selectedRoute == nil ? "Drives" : "Analysis")
      .navigationBarTitleDisplayMode(.inline)
      .toolbar {
        if browserVM.selectedRoute != nil {
          ToolbarItem(placement: .topBarLeading) {
            Button {
              browserVM.selectedRoute = nil
            } label: {
              Label("Drives", systemImage: "chevron.left")
            }
          }
        }
      }
      .onAppear { connectWithRetry() }
    }
  }

  /// Connects using cached IP (instant) or live BLE IP.
  private func connectWithRetry() {
    Task {
      let host = DeviceService.cachedIP ?? viewModel.settings.localIP
      guard let host = host, !host.isEmpty else {
        await MainActor.run {
          browserVM.error = "No device IP yet. Connect to the device via BLE once, then it's cached for SSH."
        }
        return
      }
      await browserVM.connect(host: host)
    }
  }

  private var connectingView: some View {
    VStack(spacing: 16) {
      if browserVM.isLoading {
        ProgressView("Connecting...")
      } else if let err = browserVM.error {
        Image(systemName: "exclamationmark.triangle.fill").foregroundStyle(.orange)
        // Copyable error: long-press or tap to copy the full error text
        Text(err)
          .font(.system(size: 12, design: .monospaced))
          .foregroundStyle(.secondary)
          .multilineTextAlignment(.center)
          .textSelection(.enabled)
          .padding(.horizontal, 24)
        Button("Copy error") {
          UIPasteboard.general.string = err
        }
        .buttonStyle(.bordered)
        .tint(.secondary)
        Button("Retry") {
          browserVM.error = nil
          connectWithRetry()
        }
        .buttonStyle(.bordered)
      }
    }
    .frame(maxWidth: .infinity, maxHeight: .infinity)
  }

  private var routeListView: some View {
    List {
      if browserVM.routes.isEmpty && !browserVM.isLoading {
        Text("No drives found").foregroundStyle(.secondary)
      }
      ForEach(browserVM.routes) { drive in
        Button {
          Task { await browserVM.selectRoute(drive.route) }
        } label: {
          HStack {
            Image(systemName: "car.fill").foregroundStyle(.secondary)
            VStack(alignment: .leading) {
              Text(formatRoute(drive.route)).font(.system(size: 14, weight: .medium))
              HStack(spacing: 6) {
                Text(drive.route).font(.system(size: 10, design: .monospaced)).foregroundStyle(.tertiary)
                if drive.segmentCount > 1 {
                  Text("\(drive.segmentCount) segs").font(.system(size: 10, weight: .semibold))
                    .foregroundStyle(.secondary)
                    .padding(.horizontal, 6).padding(.vertical, 1)
                    .background(Color.secondary.opacity(0.12), in: Capsule())
                }
              }
            }
            Spacer()
            Image(systemName: "chevron.right").font(.system(size: 12)).foregroundStyle(.tertiary)
          }
        }
        .buttonStyle(.plain)
      }
    }
  }

  private func formatRoute(_ route: String) -> String {
    // Route name is like "2026-08-08--03-55-22" in UTC. Parse as UTC, show in local time.
    // Strip segment suffix if present.
    let base = route.split(separator: "--").prefix(3).joined(separator: "--")
    let isoStr = base.replacingOccurrences(of: "--", with: "T")
    let df = DateFormatter()
    df.dateFormat = "yyyy-MM-dd'T'HH-mm-ss"
    df.timeZone = TimeZone(identifier: "UTC")
    guard let date = df.date(from: isoStr) else { return route }
    let outFmt = DateFormatter()
    outFmt.dateStyle = .medium
    outFmt.timeStyle = .short
    return outFmt.string(from: date)
  }
}

/// Charts view: displays steer command, curvature tracking, speed, override markers.
/// Downsampling to ~200 points keeps rendering smooth (full-res data has 1000s of points).
struct DriveChartsView: View {
  let data: DriveData
  let route: String
  let onBack: () -> Void

  /// Toggle: true = wall-clock time (default), false = elapsed (0:00 start).
  @State private var showWallClock = true

  /// Downsampled data — max 200 points per chart for smooth scrolling.
  private var ds: DownsampledData { data.downsampled(maxPoints: 200) }

  /// X-axis values for charts: epoch seconds (wall clock) or elapsed seconds.
  private var xValues: [Double] {
    if showWallClock { return ds.t }
    let first = ds.t.first ?? 0
    return ds.t.map { $0 - first }
  }

  var body: some View {
    List {
      summarySection
      steerChartSection
      pidComponentsSection
      curvatureChartSection
      speedChartSection
    }
    .listStyle(.insetGrouped)
    .scrollContentBackground(.hidden)
  }

  // MARK: Summary with plain-English tuning guidance

  private var summarySection: some View {
    Section {
      VStack(alignment: .leading, spacing: 8) {
        HStack {
          StatBox(title: "Duration", value: ds.durationString)
          StatBox(title: "Avg Speed", value: ds.avgSpeedString)
          StatBox(title: "Max Speed", value: ds.maxSpeedString)
        }
        HStack {
          StatBox(title: "Steer Avg Step", value: String(format: "%.3f", ds.avgStep))
          StatBox(title: "Steer Max Step", value: String(format: "%.3f", ds.maxStep))
          StatBox(title: "Overrides", value: "\(ds.overrides)")
        }
      }

      VStack(alignment: .leading, spacing: 4) {
        Text("What this means")
          .font(.system(size: 12, weight: .semibold))
        Text(ds.tuningGuidance)
          .font(.system(size: 12))
          .foregroundStyle(.secondary)
      }
      .padding(.top, 4)
    } header: {
      VStack(alignment: .leading, spacing: 2) {
        Text(formatRouteLocal(route))
          .font(.system(size: 14, weight: .semibold))
          .foregroundStyle(.primary)
        Text(route)
          .font(.system(size: 9, design: .monospaced))
          .foregroundStyle(.tertiary)
      }
      .textCase(nil)
    }
  }

  // MARK: Charts (downsampled, with toggleable time axis)

  private var steerChartSection: some View {
    Section {
      Chart {
        ForEach(0..<xValues.count, id: \.self) { i in
          LineMark(
            x: .value("Time", xValues[i]),
            y: .value("Cmd", ds.c[i])
          )
          .foregroundStyle(.blue)
          .lineStyle(StrokeStyle(lineWidth: 1.5))
        }
      }
      .chartYScale(domain: -1...1)
      .chartXAxis { axisMarks() }
      .frame(height: 180)
    } header: {
      HStack {
        Text("Steer Command (PID output)")
        Spacer()
        Button(showWallClock ? "🕐 Clock" : "⏱ Elapsed") {
          showWallClock.toggle()
        }
        .font(.system(size: 11, weight: .medium))
        .buttonStyle(.bordered)
        .controlSize(.small)
      }
    } footer: {
      Text("How hard the PID is steering each frame. Big jumps = jerky feel. Smooth line = good. If avg step > 0.05, consider increasing LAT_SMOOTH_SECONDS.")
        .font(.system(size: 11))
        .foregroundStyle(.secondary)
    }
  }

  private var pidComponentsSection: some View {
    Section {
      Chart {
        ForEach(0..<xValues.count, id: \.self) { i in
          LineMark(x: .value("Time", xValues[i]), y: .value("P", ds.p[i]))
            .foregroundStyle(.blue).lineStyle(StrokeStyle(lineWidth: 1))
          LineMark(x: .value("Time", xValues[i]), y: .value("I", ds.i[i]))
            .foregroundStyle(.purple).lineStyle(StrokeStyle(lineWidth: 1))
          LineMark(x: .value("Time", xValues[i]), y: .value("F", ds.f[i]))
            .foregroundStyle(.orange).lineStyle(StrokeStyle(lineWidth: 1))
        }
      }
      .chartXAxis { axisMarks() }
      .frame(height: 160)
    } header: {
      Text("PID Components: P (blue) · I (purple) · F (orange)")
    } footer: {
      Text("The individual PID contributions. P = proportional (error × kp), I = integral (accumulated error × ki), F = feedforward (model-based). If P is spiking wildly, increase LAT_SMOOTH_SECONDS. If I grows large, there's steady-state error — check if the car is fighting the PID.")
        .font(.system(size: 11))
        .foregroundStyle(.secondary)
    }
  }

  private var curvatureChartSection: some View {
    Section {
      Chart {
        ForEach(0..<xValues.count, id: \.self) { i in
          LineMark(x: .value("Time", xValues[i]),
                   y: .value("Desired", ds.d[i]))
          .foregroundStyle(.green)
          .lineStyle(StrokeStyle(lineWidth: 1.5))
          LineMark(x: .value("Time", xValues[i]),
                   y: .value("Actual", ds.a[i]))
          .foregroundStyle(.orange)
          .lineStyle(StrokeStyle(lineWidth: 1.5))
        }
      }
      .chartXAxis { axisMarks() }
      .frame(height: 180)
    } header: {
      Text("Curvature Tracking")
    } footer: {
      Text("Green = what the model wants, Orange = what the car actually does. Close together = good tracking. If orange lags or overshoots green in corners, increase kiV (integral) or steerActuatorDelay.")
        .font(.system(size: 11))
        .foregroundStyle(.secondary)
    }
  }

  private var speedChartSection: some View {
    Section {
      Chart {
        ForEach(0..<xValues.count, id: \.self) { i in
          LineMark(x: .value("Time", xValues[i]),
                   y: .value("Speed", ds.v[i] * 3.6))
          .foregroundStyle(.purple)
          .lineStyle(StrokeStyle(lineWidth: 1.5))
        }
      }
      .chartXAxis { axisMarks() }
      .frame(height: 140)
    } header: {
      Text("Speed (km/h)")
    }
  }

  /// Axis marks: stride every 60s. Labels show wall-clock (HH:mm) or elapsed (m:ss).
  @ViewBuilder
  private func axisMarks() -> some AxisContent {
    AxisMarks(values: .stride(by: 60)) { value in
      AxisGridLine()
      if let secs = value.as(Double.self) {
        AxisValueLabel(formatTime(secs))
      }
    }
  }

  /// Formats a timestamp: wall-clock mode shows HH:mm (local time), elapsed shows m:ss.
  private func formatTime(_ seconds: Double) -> String {
    if showWallClock {
      let date = Date(timeIntervalSince1970: seconds)
      let df = DateFormatter()
      df.dateFormat = "HH:mm"
      return df.string(from: date)
    }
    let m = Int(seconds) / 60
    let s = Int(seconds) % 60
    return String(format: "%d:%02d", m, s)
  }

  private func formatRouteLocal(_ route: String) -> String {
    // Route name is "2026-08-08--03-55-22" in UTC → convert to iPhone local time
    let base = route.split(separator: "--").prefix(3).joined(separator: "--")
    let isoStr = base.replacingOccurrences(of: "--", with: "T")
    let df = DateFormatter()
    df.dateFormat = "yyyy-MM-dd'T'HH-mm-ss"
    df.timeZone = TimeZone(identifier: "UTC")
    guard let date = df.date(from: isoStr) else { return route }
    let outFmt = DateFormatter()
    outFmt.dateStyle = .medium
    outFmt.timeStyle = .short
    return outFmt.string(from: date)
  }
}

struct ChartSection<Content: View>: View {
  let title: String
  let subtitle: String
  @ViewBuilder let content: Content

  var body: some View {
    VStack(alignment: .leading, spacing: 4) {
      Text(title).font(.system(size: 14, weight: .semibold))
      Text(subtitle).font(.system(size: 11)).foregroundStyle(.secondary)
      content
    }
  }
}

struct StatBox: View {
  let title: String
  let value: String

  var body: some View {
    VStack(alignment: .leading, spacing: 1) {
      Text(title).font(.system(size: 9)).foregroundStyle(.tertiary)
      Text(value).font(.system(size: 14, weight: .medium, design: .monospaced))
    }
    .padding(8)
    .background(Color.secondary.opacity(0.08), in: RoundedRectangle(cornerRadius: 6))
  }
}
