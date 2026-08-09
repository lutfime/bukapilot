import Foundation

/// ViewModel for the PID tuning panel. Handles SSH connect/disconnect,
/// fetching tuning lines, and saving changes. The view just observes.
final class TuningViewModel: ObservableObject {
  @Published var isConnected = false
  @Published var status: String = ""
  @Published var connecting = false
  @Published var sections: [DeviceService.TuningSection] = []
  @Published var editedLines: [Int: String] = [:]
  @Published var saveResult: String = ""

  private let service = DeviceService()
  private var settings: DeviceSettings = .empty

  // Host info for display
  var hostLabel: String {
    DeviceService.hostLabel(ssid: settings.activeWlanSSID)
  }
  var hostDetail: String {
    DeviceService.resolveHost(hotspotIp: settings.hotspotIp, localIP: settings.localIP) ?? "no IP"
  }
  var currentSSID: String {
    HotspotDetector.currentSSID ?? "(no SSID)"
  }
  var resolvedIP: String {
    DeviceService.resolveHost(hotspotIp: settings.hotspotIp, localIP: settings.localIP) ?? "(none)"
  }

  func updateSettings(_ s: DeviceSettings) {
    settings = s
  }

  func connect() async {
    guard !connecting else { return }
    connecting = true
    status = ""

    let host = DeviceService.resolveHost(hotspotIp: settings.hotspotIp, localIP: settings.localIP)

    do {
      guard let host = host, !host.isEmpty else {
        status = """
        No device IP found.
        WiFi: \(HotspotDetector.currentSSID ?? "(no SSID)")
        BLE localIP: \(settings.localIP ?? "nil")
        BLE hotspotIp: \(settings.hotspotIp ?? "nil")
        Cached IPs: \(DeviceService.ipCacheDescription)
        """
        connecting = false
        return
      }
      try await service.connectIfNeeded(hotspotIp: settings.hotspotIp, localIP: settings.localIP)
      isConnected = true
      connecting = false
      await fetchLines()
    } catch {
      status = """
      \(service.connectionError ?? "SSH connection failed")
      
      Host: \(host)
      WiFi SSID: \(HotspotDetector.currentSSID ?? "(no SSID)")
      BLE localIP: \(settings.localIP ?? "nil")
      BLE hotspotIp: \(settings.hotspotIp ?? "nil")
      Error: \(error)
      """
      connecting = false
    }
  }

  func disconnect() {
    service.disconnect()
    isConnected = false
  }

  func fetchLines() async {
    let fetched = await service.fetchTuningLines()
    await MainActor.run {
      self.sections = fetched
      if fetched.isEmpty {
        self.status = "No X70 tuning lines found. Is this an X70?"
        self.isConnected = false
      }
    }
  }

  func saveChanges() {
    let changed = editedLines.filter { lineNum, newValue in
      let original = sections.flatMap(\.lines).first(where: { $0.lineNum == lineNum })?.value
      return newValue != original && !newValue.isEmpty
    }
    guard !changed.isEmpty else { return }
    saveResult = "Saving..."

    Task {
      var results: [String] = []
      var hadError = false
      let allLines = sections.flatMap(\.lines)
      for (lineNum, newValue) in changed.sorted(by: { $0.key < $1.key }) {
        guard let line = allLines.first(where: { $0.lineNum == lineNum }) else { continue }
        let res = await service.saveTuningLine(lineNum: lineNum, rawLine: line.rawLine, newValue: newValue, file: line.file)
        if res.ok {
          results.append("✓ Line \(lineNum): \(res.detail)")
        } else {
          results.append("✗ Line \(lineNum): \(res.detail)")
          hadError = true
          break
        }
      }
      await MainActor.run {
        self.saveResult = results.isEmpty ? "No changes" : results.joined(separator: "\n")
        self.editedLines.removeAll()
      }
      if !hadError {
        await fetchLines()
      }
    }
  }
}
