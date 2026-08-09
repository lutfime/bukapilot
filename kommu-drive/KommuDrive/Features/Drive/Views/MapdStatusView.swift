import SwiftUI

/// Live status view for mapd_kommu. Polls the device over SSH every ~1.5s and shows
/// the parsed status: active/inactive reason, matched road, corner speed, GPS state.
/// Used as the "Map Corner" segment in the Drives tab.
struct MapdStatusView: View {
  @ObservedObject var viewModel: DriveSessionViewModel
  @StateObject private var service = DeviceService()
  @State private var status: DeviceService.MapdStatus?
  @State private var mapdRunning: Bool?  // nil = unknown, true/false = checked
  @State private var connected = false
  @State private var error: String?
  @State private var pollTask: Task<Void, Never>?

  private var hostLabel: String {
    DeviceService.hostLabel(ssid: viewModel.settings.activeWlanSSID)
  }
  private var hostDetail: String {
    DeviceService.resolveHost(hotspotIp: viewModel.settings.hotspotIp, localIP: viewModel.settings.localIP) ?? "no IP"
  }

  var body: some View {
    VStack(spacing: 0) {
      if !connected {
        connectingView
      } else {
        ScrollView {
          VStack(alignment: .leading, spacing: 14) {
            statusHeader
            if let s = status {
              detailGrid(s)
            }
            if let err = error {
              Text(err)
                .font(.system(size: 11, design: .monospaced))
                .foregroundStyle(.orange)
                .frame(maxWidth: .infinity, alignment: .leading)
            }
          }
          .padding()
        }
      }
    }
    .navigationTitle("Map Corner")
    .navigationBarTitleDisplayMode(.inline)
    .toolbar {
      ToolbarItem(placement: .topBarTrailing) {
        Button {
          Task { await refreshOnce() }
        } label: {
          Image(systemName: "arrow.clockwise")
        }
      }
    }
    .onAppear { startPolling() }
    .onDisappear { stopPolling() }
  }

  // MARK: Connecting

  private var connectingView: some View {
    VStack(spacing: 14) {
      if error == nil {
        ProgressView()
        Text("Connecting to \(hostLabel)…")
          .font(.system(size: 13))
        Text("SSH to \(hostDetail)")
          .font(.system(size: 11, design: .monospaced))
          .foregroundStyle(.tertiary)
      } else {
        Image(systemName: "exclamationmark.triangle.fill").foregroundStyle(.orange)
        Text(error!)
          .font(.system(size: 12, design: .monospaced))
          .foregroundStyle(.secondary)
          .multilineTextAlignment(.center)
          .textSelection(.enabled)
          .padding(.horizontal, 24)
        Button("Retry") { startPolling() }
          .buttonStyle(.bordered)
      }
    }
    .frame(maxWidth: .infinity, maxHeight: .infinity)
  }

  // MARK: Status header

  private var statusHeader: some View {
    let s = status
    let isActive = s?.valid == true
    return HStack(spacing: 12) {
      Circle()
        .fill(statusColor(s, isActive))
        .frame(width: 14, height: 14)
      VStack(alignment: .leading, spacing: 2) {
        Text(headerTitle(s, isActive))
          .font(.system(size: 15, weight: .semibold))
        if let s = s, isActive, let v = s.vCornerKmh {
          Text("Advisory \(String(format: "%.0f", v)) km/h")
            .font(.system(size: 12, design: .monospaced))
            .foregroundStyle(.secondary)
        }
      }
      Spacer()
      if let v = s?.vCornerKmh, isActive {
        Text("\(String(format: "%.0f", v))")
          .font(.system(size: 34, weight: .bold, design: .rounded))
          .foregroundStyle(.green)
        Text("km/h")
          .font(.system(size: 11))
          .foregroundStyle(.secondary)
      }
    }
    .padding()
    .background(.ultraThinMaterial, in: RoundedRectangle(cornerRadius: 14))
  }

  // MARK: Detail grid

  private func detailGrid(_ s: DeviceService.MapdStatus) -> some View {
    VStack(alignment: .leading, spacing: 10) {
      sectionLabel("ROUTE")
      row("Status", value: s.status.isEmpty ? "—" : s.status.replacingOccurrences(of: "_", with: " "))
      row("Road", value: s.road ?? "—")
      if let secs = s.sectionsAhead, secs > 0 {
        row("Corners ahead", value: "\(secs)")
        if let d = s.distToCorner {
          row("Next corner in", value: "\(String(format: "%.0f", d)) m")
        }
      }

      Divider().padding(.vertical, 4)
      sectionLabel("GPS")
      row("Location", value: s.location ?? "—")
      if let b = s.bearing { row("Bearing", value: "\(String(format: "%.0f", b))°") }
      if let a = s.accuracy { row("Accuracy", value: "\(String(format: "%.0f", a)) m") }
      if let sp = s.gpsSpeed { row("Speed", value: "\(String(format: "%.0f", sp * 3.6)) km/h") }

      if let ways = s.waysFetched {
        Divider().padding(.vertical, 4)
        sectionLabel("OSM")
        row("State", value: osmStateLabel(s.osmState))
        row("Roads loaded", value: "\(ways)")
      }

      if s.cacheCells != nil || s.cacheSizeMb != nil {
        Divider().padding(.vertical, 4)
        sectionLabel("CACHE")
        if let c = s.cacheCells { row("Cells", value: "\(c)") }
        if let mb = s.cacheSizeMb { row("Size", value: "\(String(format: "%.1f", mb)) / 100 MB") }
      }

      if let upd = s.updated {
        Divider().padding(.vertical, 4)
        row("Updated", value: upd)
      }
    }
    .padding()
    .background(Color.secondary.opacity(0.08), in: RoundedRectangle(cornerRadius: 14))
  }

  private func sectionLabel(_ t: String) -> some View {
    Text(t)
      .font(.system(size: 10, weight: .bold))
      .foregroundStyle(.tertiary)
      .tracking(0.5)
  }

  private func osmStateLabel(_ state: String?) -> String {
    switch state ?? "" {
    case "ready_cached": return "Ready (from cache)"
    case "ready_fetched": return "Ready (fresh download)"
    case "downloading": return "Downloading…"
    case "failed": return "Download failed"
    case "not_started": return "Not started"
    default: return "—"
    }
  }

  private func row(_ label: String, value: String) -> some View {
    HStack {
      Text(label).font(.system(size: 12)).foregroundStyle(.secondary)
      Spacer()
      Text(value).font(.system(size: 12, design: .monospaced))
        .lineLimit(1)
        .truncationMode(.middle)
    }
  }

  // MARK: Helpers

  private func statusColor(_ s: DeviceService.MapdStatus?, _ isActive: Bool) -> Color {
    if isActive { return .green }
    if mapdRunning == false { return .gray.opacity(0.4) }  // process not running (offroad)
    return .orange
  }

  private func headerTitle(_ s: DeviceService.MapdStatus?, _ isActive: Bool) -> String {
    if isActive { return "Active" }
    if mapdRunning == false { return "Offroad (process not running)" }
    if let st = s?.status, !st.isEmpty, st != "no_status_file" {
      switch st {
      case "no_gps": return "Waiting for GPS fix…"
      case "gps_poor_acc": return "GPS too inaccurate"
      case "osm_fetching": return "Fetching OSM map data…"
      case "osm_empty": return "No roads found here (OSM)"
      case "no_route_match": return "No road matched"
      case "no_corner": return "On a road — no corners ahead"
      default: return st.replacingOccurrences(of: "_", with: " ")
      }
    }
    return "Inactive"
  }

  // MARK: Polling

  private func startPolling() {
    stopPolling()
    pollTask = Task {
      await connect()
      guard !Task.isCancelled else { return }
      while !Task.isCancelled {
        await refreshOnce()
        try? await Task.sleep(nanoseconds: 1_500_000_000)  // 1.5s
      }
    }
  }

  private func stopPolling() {
    pollTask?.cancel()
    pollTask = nil
  }

  private func connect() async {
    let host = DeviceService.resolveHost(hotspotIp: viewModel.settings.hotspotIp, localIP: viewModel.settings.localIP)
    guard let host = host, !host.isEmpty else {
      await MainActor.run {
        self.error = "No device IP yet. Connect to the device via BLE once, then it's cached for SSH."
      }
      return
    }
    do {
      try await service.connectIfNeeded(hotspotIp: viewModel.settings.hotspotIp, localIP: viewModel.settings.localIP)
      await MainActor.run { self.connected = true; self.error = nil }
    } catch {
      await MainActor.run {
        self.error = "SSH connection failed: \(error)"
      }
    }
  }

  private func refreshOnce() async {
    async let s = service.fetchMapdStatus()
    async let r = service.isMapdRunning()
    let (st, running) = await (s, r)
    await MainActor.run {
      self.status = st
      self.mapdRunning = running
      if st == nil {
        self.error = "Could not read status file (SSH error)."
      } else {
        self.error = nil
      }
    }
  }
}
