import SwiftUI

/// SSH-based PID tuning: reads the PID lines from interface.py on the device,
/// shows them with line numbers (editable), and saves changes via sed over SSH.
///
/// The user taps the slider button in the top bar → this sheet opens →
/// SSH connects to the device → fetches the PID lines → displays them.
/// The user edits a line (e.g., changes a kpV value) → taps Save →
/// the change is applied via `sed` + py_compile check.
///
/// REQUIRES: SwiftSH (SPM package: https://github.com/Frugghi/SwiftSH.git).
/// Add it in Xcode → File → Add Package Dependencies, then uncomment the
/// SwiftSHExecutor in SSHExecutor.swift.
struct TuningSheet: View {
  @ObservedObject var viewModel: DriveSessionViewModel
  @Environment(\.dismiss) private var dismiss

  // SSH key (embedded — dedicated keypair for the KommuDrive app, installed on the device)
  private let sshKey = """
-----BEGIN OPENSSH PRIVATE KEY-----
b3BlbnNzaC1rZXktdjEAAAAABG5vbmUAAAAEbm9uZQAAAAAAAAABAAAAMwAAAAtzc2gtZW
QyNTUxOQAAACD0DFYRMlbmVNYhW1JP++w3ZDXGb85Hu661umqNOuFFvwAAAJhdMTHOXTEx
zgAAAAtzc2gtZWQyNTUxOQAAACD0DFYRMlbmVNYhW1JP++w3ZDXGb85Hu661umqNOuFFvw
AAAEC6Akvf4aucDWURnFE3hwC+Scggr5UjIup1HsH8eqQokfQMVhEyVuZU1iFbUk/77Ddk
NcZvzke7rrW6ao064UW/AAAADmtvbW11ZHJpdmUtYXBwAQIDBAUGBw==
-----END OPENSSH PRIVATE KEY-----
"""
  @State private var sshConnected = false
  @State private var sshStatus: String = ""

  // Tuning lines: [lineNum: code]
  @State private var lines: [(lineNum: Int, code: String)] = []
  @State private var editedLines: [Int: String] = [:]  // lineNum -> edited code
  @State private var saveResult: String = ""

  // The interface.py path on the device (both lower + merged)
  private let interfacePath = "/data/safe_staging/merged/opendbc_repo/opendbc/car/proton/interface.py"
  private let lowerPath = "/data/openpilot/opendbc_repo/opendbc/car/proton/interface.py"
  // PID line patterns to extract
  private let grepPattern = "pid.kpV\\|pid.kiV\\|pid.kf\\|longitudinalTuning.kpV\\|longitudinalTuning.kiV\\|LAT_SMOOTH_SECONDS ="

  var body: some View {
    NavigationStack {
      List {
        if !sshConnected {
          sshSetupSection
        } else {
          tuningLinesSection
          saveSection
        }
      }
      .navigationTitle("PID Tuning")
      .navigationBarTitleDisplayMode(.inline)
      .toolbar {
        ToolbarItem(placement: .topBarTrailing) {
          Button("Done") { disconnect(); dismiss() }
        }
      }
      .onAppear { checkKeyAndConnect() }
      .onDisappear { disconnect() }
    }
  }

  // MARK: SSH setup (first time: paste key, then connect)

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
        }
      }
      .frame(maxWidth: .infinity)
      .padding(.vertical, 20)
    } header: {
      Text("SSH Connection")
    }
  }

  // MARK: Tuning lines (the editable code)

  private var tuningLinesSection: some View {
    Section {
      if lines.isEmpty {
        ProgressView("Loading PID lines...")
      } else {
        ForEach(lines.indices, id: \.self) { i in
          let line = lines[i]
          VStack(alignment: .leading, spacing: 2) {
            Text("Line \(line.lineNum)")
              .font(.system(size: 10, design: .monospaced))
              .foregroundStyle(.tertiary)
            TextField("code", text: Binding(
              get: { editedLines[line.lineNum] ?? line.code },
              set: { editedLines[line.lineNum] = $0 }
            ), axis: .horizontal)
            .font(.system(size: 11, design: .monospaced))
            .textFieldStyle(.roundedBorder)
            .autocorrectionDisabled()
          }
          .padding(.vertical, 2)
        }
      }
    } header: {
      Text("PID Values (interface.py)")
    } footer: {
      Text("Edit the values in the text fields, then tap Save. Changes apply on the next drive. Speed breakpoints: kpBP = [0, 5, 15, 25, 35] m/s.")
    }
  }

  private var saveSection: some View {
    Section {
      let changed = editedLines.filter { lineNum, newCode in
        let original = lines.first(where: { $0.lineNum == lineNum })?.code
        return newCode != original && !newCode.isEmpty
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
        Text(saveResult)
          .font(.system(size: 12))
          .foregroundStyle(saveResult.contains("OK") ? .green : .red)
      }
    } header: {
      Text("Apply")
    }
  }

  // MARK: SSH operations

  private func checkKeyAndConnect() {
    connect()
  }

  private func connect() {
    sshStatus = ""
    let host = viewModel.settings.localIP ?? ""
    if host.isEmpty {
      sshStatus = "No device IP. Connect to device first."
      return
    }

    // Use the SSH executor to connect + fetch lines.
    // The executor uses SwiftSH (or a stub if the library isn't added yet).
    DispatchQueue.global(qos: .userInitiated).async {
      let executor = TuningSheet.makeExecutor()
      let connected = executor.connect(host: host, user: "kommu", privateKey: sshKey)

      DispatchQueue.main.async {
        if connected {
          self.sshConnected = true
          self.sshStatus = "Connected"
          self.executor = executor
          self.fetchLines()
        } else {
          self.sshStatus = "SSH connection failed. Check the key + device IP (\(host))."
        }
      }
    }
  }

  private func fetchLines() {
    guard let executor = executor else { return }
    DispatchQueue.global(qos: .userInitiated).async {
      // grep the PID lines with line numbers
      let cmd = "grep -n '\(grepPattern)' \(interfacePath)"
      let output = executor.execute(cmd)

      DispatchQueue.main.async {
        if let output = output, !output.isEmpty {
          self.lines = output.split(separator: "\n").compactMap { line in
            // Parse "123:ret.lateralTuning.pid.kpV  = [...]"
            let parts = line.split(separator: ":", maxSplits: 1)
            guard parts.count == 2, let lineNum = Int(parts[0]) else { return nil }
            let code = String(parts[1]).trimmingCharacters(in: .whitespaces)
            return (lineNum: lineNum, code: code)
          }
          if self.lines.isEmpty {
            self.sshStatus = "No PID lines found. Check interface.py."
          }
        } else {
          self.sshStatus = "Failed to read PID lines."
        }
      }
    }
  }

  private func saveChanges(changed: [Int: String]) {
    guard let executor = executor else { return }
    saveResult = "Saving..."

    DispatchQueue.global(qos: .userInitiated).async {
      var results: [String] = []
      for (lineNum, newCode) in changed {
        // sed to replace the line, then py_compile to check syntax.
        // Do both merged + lower paths.
        let escapedCode = newCode.replacingOccurrences(of: "'", with: "'\\''")
        let sedCmd = """
        sed -i '\(lineNum)c\\\(escapedCode)' \(self.interfacePath) \
        && sed -i '\(lineNum)c\\\(escapedCode)' \(self.lowerPath) \
        && /usr/local/venv/bin/python -m py_compile \(self.interfacePath) 2>&1 \
        && echo OK || echo SYNTAX_ERROR
        """
        let result = executor.execute(sedCmd) ?? "NO_RESPONSE"
        results.append("Line \(lineNum): \(result.contains("OK") ? "✓" : "✗ \(result)")")

        // If syntax error, stop — don't apply more changes.
        if result.contains("SYNTAX_ERROR") || result.contains("Error") {
          break
        }
      }

      DispatchQueue.main.async {
        self.saveResult = results.joined(separator: "\n")
        // Refresh the displayed lines
        self.fetchLines()
        self.editedLines.removeAll()
      }
    }
  }

  private func disconnect() {
    executor?.disconnect()
    executor = nil
    sshConnected = false
  }

  // Holds the SSH executor instance (set after connect)
  @State private var executor: AnySSHExecutorBox?

  /// Creates the SSH executor. Replace StubSSHExecutor with SwiftSHExecutor
  /// after adding the SwiftSH package.
  static func makeExecutor() -> AnySSHExecutorBox {
    // TODO: Uncomment after adding SwiftSH via SPM:
    // return AnySSHExecutorBox(SwiftSHExecutor())
    return AnySSHExecutorBox(StubSSHExecutor())
  }
}

// MARK: - SSH Executor Protocol

/// Protocol for executing shell commands on the device via SSH.
protocol SSHExecutor: AnyObject {
  func connect(host: String, user: String, privateKey: String) -> Bool
  func execute(_ command: String) -> String?
  func disconnect()
}

/// Type-erased wrapper (so @State can hold it).
final class AnySSHExecutorBox: SSHExecutor {
  private let _connect: (String, String, String) -> Bool
  private let _execute: (String) -> String?
  private let _disconnect: () -> Void

  init<E: SSHExecutor>(_ executor: E) {
    _connect = executor.connect
    _execute = executor.execute
    _disconnect = executor.disconnect
  }
  func connect(host: String, user: String, privateKey: String) -> Bool { _connect(host, user, privateKey) }
  func execute(_ command: String) -> String? { _execute(command) }
  func disconnect() { _disconnect() }
}

/// Stub — returns failure until SwiftSH is added.
/// After adding SwiftSH (https://github.com/Frugghi/SwiftSH.git via SPM),
/// replace this with SwiftSHExecutor (below) in `makeExecutor()`.
final class StubSSHExecutor: SSHExecutor {
  func connect(host: String, user: String, privateKey: String) -> Bool {
    return false  // SwiftSH not added yet
  }
  func execute(_ command: String) -> String? { return nil }
  func disconnect() {}
}

// MARK: - Citadel Implementation
//
// Requires: Citadel SPM package.
// Xcode → File → Add Package Dependencies → https://github.com/orlandos-nl/Citadel.git
// Then uncomment this block + change makeExecutor() to return CitadelSSHExecutor.
//
// import Citadel
// import NIOSSH
//
// final class CitadelSSHExecutor: SSHExecutor {
//   private var client: SSHClient?
//
//   func connect(host: String, user: String, privateKey: String) -> Bool {
//     // Citadel uses async/await — bridge to sync via semaphore (called from background thread)
//     let semaphore = DispatchSemaphore(value: 0)
//     var connected = false
//     Task {
//       do {
//         let parsedKey = try NIOSSHPrivateKey(file: privateKey)
//         let settings = SSHClientSettings(
//           host: host,
//           port: 22,
//           authenticationMethod: .privateKey(parsedKey),
//           hostKeyValidator: .acceptAnything()
//         )
//         self.client = try await SSHClient.connect(to: settings)
//         connected = true
//       } catch {
//         AppLog.error("Citadel connect failed: \(error)")
//       }
//       semaphore.signal()
//     }
//     semaphore.wait()
//     return connected
//   }
//
//   func execute(_ command: String) -> String? {
//     guard let client = client else { return nil }
//     let semaphore = DispatchSemaphore(value: 0)
//     var output: String?
//     Task {
//       do {
//         let buf = try await client.executeCommand(command)
//         output = String(buffer: buf)
//       } catch {
//         AppLog.error("Citadel execute failed: \(error)")
//       }
//       semaphore.signal()
//     }
//     semaphore.wait()
//     return output
//   }
//
//   func disconnect() {
//     client = nil
//   }
// }
