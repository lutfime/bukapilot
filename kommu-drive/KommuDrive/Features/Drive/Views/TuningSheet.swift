import SwiftUI

/// SSH-based PID tuning panel: reads the X70 PID/longitudinal/steer lines from
/// interface.py on the device, grouped into sections, and saves changes via
/// sed over SSH with a py_compile syntax check.
///
/// Shown inline as a tab panel (no modal sheet). The bottom tab bar handles
/// navigation, so there's no Done/dismiss button.
struct TuningSheet: View {
  @ObservedObject var viewModel: DriveSessionViewModel
  @StateObject private var device = DeviceService()

  @State private var sshConnected = false
  @State private var sshStatus: String = ""
  @State private var connecting = false

  @State private var sections: [DeviceService.TuningSection] = []
  @State private var editedLines: [Int: String] = [:]  // lineNum -> edited code
  @State private var saveResult: String = ""

  var body: some View {
    NavigationStack {
      List {
        if !sshConnected {
          sshSetupSection
        } else {
          ForEach(sections) { section in
            tuningSection(section)
          }
          if !sections.isEmpty {
            saveSection
          }
        }
      }
      .navigationTitle("PID Tuning")
      .navigationBarTitleDisplayMode(.inline)
    }
    .onAppear { connect() }
    .onDisappear { device.disconnect(); sshConnected = false }
  }

  // MARK: SSH setup (connecting / error / retry)

  private var sshSetupSection: some View {
    Section {
      VStack(spacing: 12) {
        if sshStatus.isEmpty {
          ProgressView("Connecting to device...")
        } else {
          Image(systemName: "exclamationmark.triangle.fill")
            .foregroundStyle(.orange)
          Text(sshStatus)
            .font(.system(size: 13))
            .foregroundStyle(.secondary)
            .multilineTextAlignment(.center)
          Button("Retry") { connect() }
            .buttonStyle(.bordered)
        }
      }
      .frame(maxWidth: .infinity)
      .padding(.vertical, 20)
    } header: {
      Text("SSH Connection")
    }
  }

  // MARK: A tuning section (Lateral / Longitudinal / Steer)

  private func tuningSection(_ section: DeviceService.TuningSection) -> some View {
    Section {
      ForEach(section.lines) { line in
        VStack(alignment: .leading, spacing: 3) {
          HStack {
            Text(line.label)
              .font(.system(size: 11, weight: .medium))
            Spacer()
            Text("L\(line.lineNum)")
              .font(.system(size: 9, design: .monospaced))
              .foregroundStyle(.tertiary)
          }
          TextField("value", text: Binding(
            get: { editedLines[line.lineNum] ?? line.value },
            set: { editedLines[line.lineNum] = $0 }
          ), axis: .horizontal)
          .font(.system(size: 11, design: .monospaced))
          .textFieldStyle(.roundedBorder)
          .autocorrectionDisabled()
        }
        .padding(.vertical, 2)
      }
    } header: {
      Text(section.name)
    }
  }

  private var saveSection: some View {
    Section {
      let changed = editedLines.filter { lineNum, newValue in
        let original = sections.flatMap(\.lines).first(where: { $0.lineNum == lineNum })?.value
        return newValue != original && !newValue.isEmpty
      }
      Button {
        saveChanges(changed: changed)
      } label: {
        HStack {
          Image(systemName: "checkmark.circle.fill")
          Text("Save \(changed.count) change\(changed.count == 1 ? "" : "s")")
        }
      }
      .buttonStyle(.borderedProminent)
      .tint(.green)
      .disabled(changed.isEmpty)

      if !saveResult.isEmpty {
        VStack(alignment: .leading, spacing: 6) {
          Text(saveResult)
            .font(.system(size: 11, design: .monospaced))
            .foregroundStyle(saveResult.hasPrefix("✓") ? .green : .red)
            .textSelection(.enabled)
          if saveResult.contains("✗") {
            Button("Copy error") {
              UIPasteboard.general.string = saveResult
            }
            .buttonStyle(.bordered)
            .tint(.secondary)
          }
        }
      }
    } header: {
      Text("Apply")
    } footer: {
      Text("Changes apply on the next drive. Each save is syntax-checked (py_compile) before committing.")
    }
  }

  // MARK: SSH operations

  /// Connects to the device. Uses the cached IP (from last successful connect)
  /// for instant reconnect — no need to wait for BLE. Falls back to the live
  /// BLE settings IP if no cache exists.
  private func connect() {
    guard !connecting else { return }
    connecting = true
    sshStatus = ""
    sshConnected = false

    Task {
      // Prefer cached IP (instant), fall back to live BLE IP.
      let host = DeviceService.cachedIP ?? viewModel.settings.localIP
      await MainActor.run { connecting = false }
      guard let host = host, !host.isEmpty else {
        sshStatus = "No device IP yet. Connect to the device via BLE once, then it's cached for SSH."
        return
      }
      do {
        try await device.connect(host: host)
        await MainActor.run { sshConnected = true }
        await fetchLines()
      } catch {
        await MainActor.run {
          sshStatus = device.connectionError ?? "SSH connection failed: \(error.localizedDescription)"
        }
      }
    }
  }

  private func fetchLines() {
    Task {
      let fetched = await device.fetchTuningLines()
      await MainActor.run {
        self.sections = fetched
        if fetched.isEmpty {
          self.sshStatus = "No X70 tuning lines found. Is this an X70?"
          self.sshConnected = false
        }
      }
    }
  }

  private func saveChanges(changed: [Int: String]) {
    saveResult = "Saving..."
    Task {
      var results: [String] = []
      var hadError = false
      // Build a map of lineNum → rawLine for the save call
      let allLines = sections.flatMap(\.lines)
      for (lineNum, newValue) in changed.sorted(by: { $0.key < $1.key }) {
        guard let line = allLines.first(where: { $0.lineNum == lineNum }) else { continue }
        let res = await device.saveTuningLine(lineNum: lineNum, rawLine: line.rawLine, newValue: newValue, file: line.file)
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
      }
      if hadError {
        // Revert the failed edit in the UI — clear editedLines so text fields
        // fall back to the original device values.
        await MainActor.run { self.editedLines.removeAll() }
      } else {
        await fetchLines()
        await MainActor.run { self.editedLines.removeAll() }
      }
    }
  }
}
