import SwiftUI

/// Device log viewer. Fetches swaglog entries from the device via SSH.
/// Shows MADS debug logs, recent selfdrived/controlsd errors, and process status.
struct LogsView: View {
  @ObservedObject var viewModel: DriveSessionViewModel

  @State private var logs: String = ""
  @State private var loading = false
  @State private var error: String?
  @State private var selectedCategory: LogCategory = .mads

  private let service = DeviceService()

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

  var body: some View {
    NavigationStack {
      VStack(spacing: 0) {
        // Category picker
        Picker("Category", selection: $selectedCategory) {
          ForEach(LogCategory.allCases, id: \.self) { cat in
            Text(cat.rawValue).tag(cat)
          }
        }
        .pickerStyle(.segmented)
        .padding(.horizontal, 12)
        .padding(.top, 8)
        .onChange(of: selectedCategory) { _, _ in
          Task { await fetchLogs() }
        }

        // Toolbar: refresh + clear
        HStack {
          Button {
            Task { await fetchLogs() }
          } label: {
            Label("Refresh", systemImage: "arrow.clockwise")
          }
          .disabled(loading)

          Spacer()

          if loading { ProgressView().scaleEffect(0.7) }

          if !logs.isEmpty {
            Button {
              UIPasteboard.general.string = logs
            } label: {
              Image(systemName: "doc.on.doc")
            }
          }
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 6)

        // Log content
        if let error = error {
          Spacer()
          VStack(spacing: 12) {
            Image(systemName: "exclamationmark.triangle")
              .font(.system(size: 32))
              .foregroundStyle(.orange)
            Text(error)
              .font(.system(size: 13, design: .monospaced))
              .foregroundStyle(.secondary)
              .multilineTextAlignment(.center)
              .padding(.horizontal, 24)
          }
          Spacer()
        } else if logs.isEmpty && !loading {
          Spacer()
          VStack(spacing: 12) {
            Image(systemName: "doc.text.magnifyingglass")
              .font(.system(size: 32))
              .foregroundStyle(.secondary)
            Text("No logs yet")
              .font(.system(size: 14))
              .foregroundStyle(.secondary)
            Text("Tap Refresh to fetch from device")
              .font(.system(size: 12))
              .foregroundStyle(.tertiary)
          }
          Spacer()
        } else {
          ScrollView {
            Text(formatLogs(logs))
              .font(.system(size: 11, design: .monospaced))
              .frame(maxWidth: .infinity, alignment: .leading)
              .padding(10)
          }
        }
      }
      .navigationTitle("Logs")
      .navigationBarTitleDisplayMode(.inline)
      .task {
        await fetchLogs()
      }
    }
  }

  private func fetchLogs() async {
    loading = true
    error = nil

    do {
      try await service.connectIfNeeded(
        hotspotIp: viewModel.settings.hotspotIp,
        localIP: viewModel.settings.localIP
      )
      let result = try await service.execute(selectedCategory.command)
      logs = result.trimmingCharacters(in: .whitespacesAndNewlines)
    } catch {
      self.error = """
        \(error.localizedDescription)

        Host: \(DeviceService.resolveHost(hotspotIp: viewModel.settings.hotspotIp, localIP: viewModel.settings.localIP) ?? "(none)")

        \(service.connectionError ?? "")
        """
      logs = ""
    }

    loading = false
  }

  /// Pretty-print JSON log lines: extract timestamp + message, drop noise.
  private func formatLogs(_ raw: String) -> String {
    var lines: [String] = []
    for line in raw.split(separator: "\n") {
      // Try to parse as JSON and extract the useful fields
      if let data = line.data(using: .utf8),
         let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any] {
        let created = json["created"] as? Double ?? 0
        let ts = String(format: "%.1f", created)
        let daemon = (json["ctx"] as? [String: Any])?["daemon"] as? String ?? "?"
        let level = json["level"] as? String ?? "?"

        // Message can be a string (msg$s) or a dict (msg)
        var msg: String
        if let s = json["msg$s"] as? String {
          msg = s
        } else if let s = json["msg"] as? String {
          msg = s
        } else if let dict = json["msg"] as? [String: Any] {
          // Extract key fields from dict-style messages
          let event = dict["event$s"] as? String ?? ""
          let parts = dict.map { "\($0)=\($1)" }.sorted().joined(separator: " ")
          msg = event.isEmpty ? parts : "\(event) \(parts)"
        } else {
          msg = "(unknown msg format)"
        }

        // For MADS_DEBUG, extract just the MADS_DEBUG line (it's embedded in msg$s)
        if msg.contains("MADS_DEBUG") {
          lines.append("[\(ts)] \(msg)")
        } else {
          lines.append("[\(ts)] [\(daemon)/\(level)] \(msg)")
        }
      } else {
        // Not JSON — show raw line
        lines.append(String(line))
      }
    }
    return lines.joined(separator: "\n")
  }
}
