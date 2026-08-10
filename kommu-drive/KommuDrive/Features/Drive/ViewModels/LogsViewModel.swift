import Foundation

/// ViewModel for the device log viewer. Handles SSH connection, fetching
/// filtered swaglog entries, and parsing JSON log lines. The view just observes.
final class LogsViewModel: ObservableObject {
  @Published var logs: String = ""
  @Published var loading = false
  @Published var error: String?
  @Published var selectedCategory: LogCategory = .mads

  private let service = DeviceService()
  private var settings: DeviceSettings = .empty

  enum LogCategory: String, CaseIterable {
    case mads = "MADS"
    case selfdrived = "selfdrived"
    case controlsd = "controlsd"
    case recent = "Recent"

    var command: String {
      switch self {
      case .mads:
        return "grep -h MADS_DEBUG /data/log/swaglog.* 2>/dev/null | tail -60"
      case .selfdrived:
        return "python3 -c \""
          + "import sys,json;"
          + "[print(json.loads(l).get('msg$s', json.loads(l).get('msg',{}).get('event$s',''))[:200]) "
          + "for l in sys.stdin "
          + "if '\\\"daemon\\\": \\\"selfdrived\\\"' in l and 'git diff' not in l]\" "
          + "/data/log/swaglog.* 2>/dev/null | tail -30"
      case .controlsd:
        return "python3 -c \""
          + "import sys,json;"
          + "[print(json.loads(l).get('msg$s', json.loads(l).get('msg',{}).get('event$s',''))[:200]) "
          + "for l in sys.stdin "
          + "if '\\\"daemon\\\": \\\"controlsd\\\"' in l and 'git diff' not in l]\" "
          + "/data/log/swaglog.* 2>/dev/null | tail -30"
      case .recent:
        return "python3 -c \""
          + "import sys,json;"
          + "[print(f\\\"[{json.loads(l).get('ctx',{}).get('daemon','?')}] {json.loads(l).get('msg$s', json.loads(l).get('msg',{}).get('event$s',''))[:200]}\\\") "
          + "for l in sys.stdin "
          + "if 'git diff' not in l]\" "
          + "/data/log/swaglog.* 2>/dev/null | tail -40"
      }
    }
  }

  // MARK: - Settings

  @MainActor
  func updateSettings(_ s: DeviceSettings) {
    settings = s
  }

  var resolvedIP: String {
    DeviceService.resolveHost(hotspotIp: settings.hotspotIp, localIP: settings.localIP) ?? "(none)"
  }

  // MARK: - Fetch

  @MainActor
  func selectCategory(_ cat: LogCategory) {
    selectedCategory = cat
    Task { await fetchLogs() }
  }

  @MainActor
  func fetchLogs() async {
    loading = true
    error = nil

    do {
      try await service.connectIfNeeded(
        hotspotIp: settings.hotspotIp,
        localIP: settings.localIP
      )
      let result = try await service.execute(selectedCategory.command)
      logs = result.trimmingCharacters(in: .whitespacesAndNewlines)
    } catch let err {
      error = """
      \(err.localizedDescription)

      Host: \(resolvedIP)

      \(service.connectionError ?? "")
      """
      logs = ""
    }

    loading = false
  }
}
