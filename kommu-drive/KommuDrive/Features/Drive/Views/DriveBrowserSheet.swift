import SwiftUI
import Charts

/// Drive browser: lists drives on the device → tap one → see charts (steer, curvature, speed).
/// Uses DriveBrowserViewModel (which uses DeviceService via SSH) — full UI, SSH invisible.
struct DriveBrowserSheet: View {
  @ObservedObject var viewModel: DriveSessionViewModel
  @Environment(\.dismiss) private var dismiss
  @StateObject private var browserVM = DriveBrowserViewModel()

  var body: some View {
    NavigationStack {
      Group {
        if !browserVM.isConnected {
          connectingView
        } else if let route = browserVM.selectedRoute {
          if browserVM.isLoading {
              ProgressView("Loading \(route)...")
          } else if let data = browserVM.driveData {
            DriveChartsView(data: data, route: route) {
              browserVM.selectedRoute = nil
            }
          } else if let err = browserVM.error {
            Text(err).foregroundStyle(.red)
          }
        } else {
          routeListView
        }
      }
      .navigationTitle(browserVM.selectedRoute == nil ? "Drives" : "Analysis")
      .navigationBarTitleDisplayMode(.inline)
      .toolbar {
        ToolbarItem(placement: .topBarTrailing) {
          Button("Done") { browserVM.disconnect(); dismiss() }
        }
      }
      .onAppear {
        Task {
          let host = viewModel.settings.localIP ?? ""
          await browserVM.connect(host: host)
        }
      }
    }
  }

  private var connectingView: some View {
    VStack(spacing: 16) {
      if browserVM.isLoading {
        ProgressView("Connecting...")
      } else if let err = browserVM.error {
        Image(systemName: "exclamationmark.triangle.fill").foregroundStyle(.orange)
        Text(err).font(.system(size: 13)).foregroundStyle(.secondary).multilineTextAlignment(.center)
      }
    }
    .frame(maxWidth: .infinity, maxHeight: .infinity)
  }

  private var routeListView: some View {
    List {
      if browserVM.routes.isEmpty && !browserVM.isLoading {
        Text("No drives found").foregroundStyle(.secondary)
      }
      ForEach(browserVM.routes, id: \.self) { route in
        Button {
          Task { await browserVM.selectRoute(route) }
        } label: {
          HStack {
            Image(systemName: "car.fill").foregroundStyle(.secondary)
            VStack(alignment: .leading) {
              Text(formatRoute(route)).font(.system(size: 14, weight: .medium))
              Text(route).font(.system(size: 10, design: .monospaced)).foregroundStyle(.tertiary)
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
    // "2026-08-08--03-55-22--7" → "Aug 8, 3:55 PM"
    let parts = route.split(separator: "-")
    guard parts.count >= 4 else { return route }
    let dateStr = "\(parts[0])-\(parts[1])-\(parts[2])"
    let timeStr = "\(parts[3]):\(parts[4])"
    return "\(dateStr) \(timeStr)"
  }
}

/// Charts view: displays steer command, curvature tracking, speed, override markers.
struct DriveChartsView: View {
  let data: DriveData
  let route: String
  let onBack: () -> Void

  var body: some View {
    ScrollView {
      VStack(alignment: .leading, spacing: 20) {
        // Summary stats
        summarySection

        // Steer command over time
        ChartSection(title: "Steer Command", subtitle: "PID output (the 'step' size)") {
          Chart {
            ForEach(0..<data.t.count, id: \.self) { i in
              LineMark(
                x: .value("Time", data.t[i] - (data.t.first ?? 0)),
                y: .value("Cmd", data.c[i])
              )
              .foregroundStyle(.blue)
            }
          }
          .chartYScale(domain: -1...1)
          .frame(height: 200)
        }

        // Desired vs actual curvature
        ChartSection(title: "Curvature Tracking", subtitle: "Desired (green) vs Actual (orange)") {
          Chart {
            ForEach(0..<data.t.count, id: \.self) { i in
              LineMark(x: .value("Time", data.t[i] - (data.t.first ?? 0)),
                       y: .value("Desired", data.d[i]))
              .foregroundStyle(.green)
              LineMark(x: .value("Time", data.t[i] - (data.t.first ?? 0)),
                       y: .value("Actual", data.a[i]))
              .foregroundStyle(.orange)
            }
          }
          .frame(height: 200)
        }

        // Speed profile
        ChartSection(title: "Speed", subtitle: "km/h") {
          Chart {
            ForEach(0..<data.t.count, id: \.self) { i in
              LineMark(x: .value("Time", data.t[i] - (data.t.first ?? 0)),
                       y: .value("Speed", data.v[i] * 3.6))
              .foregroundStyle(.purple)
            }
          }
          .frame(height: 150)
        }
      }
      .padding()
    }
  }

  private var summarySection: some View {
    let v = data.vKmh
    let steps = zip(data.c.dropFirst(), data.c).map { abs($0 - $1) }
    let avgStep = steps.isEmpty ? 0 : steps.reduce(0, +) / Double(steps.count)
    let maxStep = steps.max() ?? 0
    let overrides = data.o.filter { $0 == 1 }.count

    return VStack(alignment: .leading, spacing: 4) {
      Text(route).font(.system(size: 11, design: .monospaced)).foregroundStyle(.tertiary)
      HStack {
        StatBox(title: "Samples", value: "\(data.t.count)")
        StatBox(title: "Avg Speed", value: String(format: "%.0f km/h", v.isEmpty ? 0 : v.reduce(0,+) / Double(v.count)))
        StatBox(title: "Overrides", value: "\(overrides)")
      }
      HStack {
        StatBox(title: "Avg Step", value: String(format: "%.3f", avgStep))
        StatBox(title: "Max Step", value: String(format: "%.3f", maxStep))
      }
    }
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
