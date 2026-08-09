import SwiftUI
import Charts

/// Charts view: displays steer command, PID components, curvature tracking, speed.
/// Uses DriveChartsViewModel which holds all precomputed data. The view never
/// does heavy computation — it reads from @Published properties.
struct DriveChartsView: View {
  @ObservedObject var vm: DriveChartsViewModel

  @State private var showShareSheet = false

  var body: some View {
    Group {
      if !vm.isLoaded {
        ProgressView("Loading charts…")
      } else {
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
    }
    .toolbar {
      ToolbarItem(placement: .topBarTrailing) {
        Button {
          showShareSheet = true
        } label: {
          Image(systemName: "square.and.arrow.up")
        }
      }
    }
    .sheet(isPresented: $showShareSheet) {
      if !vm.qlogFiles.isEmpty {
        ShareSheet(items: vm.qlogFiles)
      }
    }
  }

  // MARK: Summary

  private var summarySection: some View {
    Section {
      VStack(alignment: .leading, spacing: 8) {
        HStack {
          StatBox(title: "Duration", value: vm.durationString)
          StatBox(title: "Avg Speed", value: vm.avgSpeedString)
          StatBox(title: "Max Speed", value: vm.maxSpeedString)
        }
        HStack {
          StatBox(title: "Steer Avg Step", value: String(format: "%.3f", vm.avgStep))
          StatBox(title: "Steer Max Step", value: String(format: "%.3f", vm.maxStep))
          StatBox(title: "Overrides", value: "\(vm.overrides)")
        }
      }

      VStack(alignment: .leading, spacing: 4) {
        Text("What this means")
          .font(.system(size: 12, weight: .semibold))
        Text(vm.tuningGuidance)
          .font(.system(size: 12))
          .foregroundStyle(.secondary)
      }
      .padding(.top, 4)
    } header: {
      VStack(alignment: .leading, spacing: 2) {
        Text(vm.dateString)
          .font(.system(size: 14, weight: .semibold))
          .foregroundStyle(.primary)
        Text(vm.route.rawValue)
          .font(.system(size: 9, design: .monospaced))
          .foregroundStyle(.tertiary)
      }
      .textCase(nil)
    }
  }

  // MARK: Charts

  private var steerChartSection: some View {
    Section {
      Chart(vm.steerData) { point in
        LineMark(
          x: .value("Time", point.x),
          y: .value("Cmd", point.y)
        )
        .foregroundStyle(.blue)
        .lineStyle(StrokeStyle(lineWidth: 1.5))
      }
      .chartXAxis { axisMarks() }
      .frame(height: 180)
    } header: {
      HStack {
        Text("Steer Command (PID output)")
        Spacer()
        Button(vm.showWallClock ? "🕐 Clock" : "⏱ Elapsed") {
          vm.showWallClock.toggle()
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
        ForEach(vm.pidP) { p in
          LineMark(x: .value("Time", p.x), y: .value("P", p.y))
            .foregroundStyle(.blue).lineStyle(StrokeStyle(lineWidth: 1))
        }
        ForEach(vm.pidI) { p in
          LineMark(x: .value("Time", p.x), y: .value("I", p.y))
            .foregroundStyle(.purple).lineStyle(StrokeStyle(lineWidth: 1))
        }
        ForEach(vm.pidF) { p in
          LineMark(x: .value("Time", p.x), y: .value("F", p.y))
            .foregroundStyle(.orange).lineStyle(StrokeStyle(lineWidth: 1))
        }
      }
      .chartXAxis { axisMarks() }
      .frame(height: 160)
    } header: {
      Text("PID Components: P (blue) · I (purple) · F (orange)")
    } footer: {
      Text("P = proportional (error × kp), I = integral (accumulated error × ki), F = feedforward. If P is spiking wildly, increase LAT_SMOOTH_SECONDS. If I grows large, there's steady-state error.")
        .font(.system(size: 11))
        .foregroundStyle(.secondary)
    }
  }

  private var curvatureChartSection: some View {
    Section {
      Chart {
        ForEach(vm.curveDesired) { p in
          LineMark(x: .value("Time", p.x), y: .value("Desired", p.y))
            .foregroundStyle(.green)
            .lineStyle(StrokeStyle(lineWidth: 1.5))
        }
        ForEach(vm.curveActual) { p in
          LineMark(x: .value("Time", p.x), y: .value("Actual", p.y))
            .foregroundStyle(.orange)
            .lineStyle(StrokeStyle(lineWidth: 1.5))
        }
      }
      .chartXAxis { axisMarks() }
      .frame(height: 180)
    } header: {
      Text("Curvature Tracking")
    } footer: {
      Text("Green = what the model wants, Orange = what the car actually does. Close together = good tracking. If orange lags or overshoots, increase kiV or steerActuatorDelay.")
        .font(.system(size: 11))
        .foregroundStyle(.secondary)
    }
  }

  private var speedChartSection: some View {
    Section {
      Chart(vm.speedData) { point in
        LineMark(x: .value("Time", point.x), y: .value("Speed", point.y))
          .foregroundStyle(.purple)
          .lineStyle(StrokeStyle(lineWidth: 1.5))
      }
      .chartXAxis { axisMarks() }
      .frame(height: 140)
    } header: {
      Text("Speed (km/h)")
    }
  }

  // MARK: Helpers

  private static let timeFormatter: DateFormatter = {
    let f = DateFormatter()
    f.dateFormat = "HH:mm"
    return f
  }()

  @ViewBuilder
  private func axisMarks() -> some AxisContent {
      AxisMarks(values: .automatic) { value in
      AxisGridLine()
      if let secs = value.as(Double.self) {
        AxisValueLabel(formatTime(secs))
      }
    }
  }

  private func formatTime(_ seconds: Double) -> String {
    if vm.showWallClock {
      let date = Date(timeIntervalSince1970: seconds)
      return Self.timeFormatter.string(from: date)
    }
    let m = Int(seconds) / 60
    let s = Int(seconds) % 60
    return String(format: "%d:%02d", m, s)
  }
}
