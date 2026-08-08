import Foundation

/// Data provider service: handles all SSH communication with the device.
/// Used by both TuningSheet (PID editing) and DriveBrowserSheet (route analysis).
/// Uses Citadel (NIOSSH) for the SSH connection — pure Swift, no C deps.
///
/// REQUIRES: Citadel SPM package (https://github.com/orlandos-nl/Citadel.git)
/// Uncomment the Citadel imports + connect/execute to enable.
final class DeviceService: ObservableObject {

  // Embedded SSH key (dedicated keypair, installed on device in /data/params/d/GithubSshKeys)
  private let sshKey = """
-----BEGIN OPENSSH PRIVATE KEY-----
b3BlbnNzaC1rZXktdjEAAAAABG5vbmUAAAAEbm9uZQAAAAAAAAABAAAAMwAAAAtzc2gtZW
QyNTUxOQAAACD0DFYRMlbmVNYhW1JP++w3ZDXGb85Hu661umqNOuFFvwAAAJhdMTHOXTEx
zgAAAAtzc2gtZWQyNTUxOQAAACD0DFYRMlbmVNYhW1JP++w3ZDXGb85Hu661umqNOuFFvw
AAAEC6Akvf4aucDWURnFE3hwC+Scggr5UjIup1HsH8eqQokfQMVhEyVuZU1iFbUk/77Ddk
NcZvzke7rrW6ao064UW/AAAADmtvbW11ZHJpdmUtYXBwAQIDBAUGBw==
-----END OPENSSH PRIVATE KEY-----
"""

  private let interfacePath = "/data/safe_staging/merged/opendbc_repo/opendbc/car/proton/interface.py"
  private let lowerPath = "/data/openpilot/opendbc_repo/opendbc/car/proton/interface.py"

  // MARK: - Citadel SSH connection (uncomment after adding Citadel SPM package)
  //
  // import Citadel
  // import NIOSSH
  // private var client: SSHClient?
  //
  // func connect(host: String) async -> Bool {
  //   do {
  //     let tempURL = FileManager.default.temporaryDirectory.appendingPathComponent("kd_ssh_key")
  //     try sshKey.write(to: tempURL, atomically: true, encoding: .utf8)
  //     let nioKey = try NIOSSHPrivateKey.fromFile(path: tempURL.path)
  //     try? FileManager.default.removeItem(at: tempURL)
  //     let settings = SSHClientSettings(
  //       host: host, port: 22,
  //       authenticationMethod: .privateKey(nioKey),
  //       hostKeyValidator: .acceptAnything()
  //     )
  //     self.client = try await SSHClient.connect(to: settings)
  //     return true
  //   } catch { AppLog.error("SSH connect failed: \(error)"); return false }
  // }
  //
  // func execute(_ command: String) async -> String? {
  //   guard let client = client else { return nil }
  //   do {
  //     let buf = try await client.executeCommand(command)
  //     return String(buffer: buf)
  //   } catch { AppLog.error("SSH execute failed: \(error)"); return nil }
  // }
  //
  // func disconnect() { client = nil }

  // Stub (no Citadel yet) — returns failures so the UI shows "SSH not connected"
  func connect(host: String) async -> Bool { return false }
  func execute(_ command: String) async -> String? { return nil }
  func disconnect() {}

  // MARK: - Tuning (PID line editing)

  func fetchTuningLines() async -> [(lineNum: Int, code: String)] {
    let cmd = "grep -n 'pid.kpV\\|pid.kiV\\|pid.kf\\|longitudinalTuning.kpV\\|longitudinalTuning.kiV\\|LAT_SMOOTH_SECONDS =' \(interfacePath)"
    guard let output = await execute(cmd), !output.isEmpty else { return [] }
    return output.split(separator: "\n").compactMap { line in
      let parts = line.split(separator: ":", maxSplits: 1)
      guard parts.count == 2, let n = Int(parts[0]) else { return nil }
      return (lineNum: n, code: String(parts[1]).trimmingCharacters(in: .whitespaces))
    }
  }

  func saveTuningLine(lineNum: Int, content: String) async -> Bool {
    let escaped = content.replacingOccurrences(of: "'", with: "'\\''")
    let cmd = "sed -i '\(lineNum)c\\\(escaped)' \(interfacePath) && sed -i '\(lineNum)c\\\(escaped)' \(lowerPath) && /usr/local/venv/bin/python -m py_compile \(interfacePath) 2>&1 && echo OK || echo FAIL"
    let result = await execute(cmd) ?? ""
    return result.contains("OK")
  }

  // MARK: - Route browsing

  func fetchRoutes() async -> [String] {
    guard let output = await execute("ls -1t /data/media/0/realdata/ | grep -v boot | head -20") else { return [] }
    return output.split(separator: "\n").map { String($0) }
  }

  func fetchDriveData(route: String) async -> DriveData? {
    // Python script runs on device: reads qlog, extracts signals, outputs JSON
    let script = """
cd /data/openpilot && /usr/local/venv/bin/python -c "
import json, zstandard
from cereal import log as capnp_log
EV = capnp_log.Event
path = '/data/media/0/realdata/\(route)--0/qlog.zst'
try:
  with open(path, 'rb') as f: d = f.read()
  try: events = list(EV.read_multiple_bytes(d))
  except: events = list(EV.read_multiple_bytes(zstandard.ZstdDecompressor().decompress(d)))
except: print(json.dumps({})); exit()
data = {'t':[], 'v':[], 'c':[], 'd':[], 'a':[], 'o':[]}
for e in events:
  try: w = e.which()
  except: continue
  if w == 'carState':
    data['v'].append(round(float(e.carState.vEgo), 2))
    data['o'].append(1 if e.carState.steeringPressed else 0)
  elif w == 'controlsState':
    data['t'].append(round(e.logMonoTime/1e9, 2))
    data['c'].append(round(float(e.controlsState.lateralControlState.pidState.output), 4))
    data['d'].append(round(float(e.controlsState.desiredCurvature), 5))
    data['a'].append(round(float(e.controlsState.curvature), 5))
print(json.dumps(data))
"
"""
    guard let output = await execute(script), let data = output.data(using: .utf8) else { return nil }
    return try? JSONDecoder().decode(DriveData.self, from: data)
  }
}

/// Parsed drive data (from the device's qlog via SSH).
struct DriveData: Codable {
  var t: [Double] = []    // timestamps (s)
  var v: [Double] = []    // vEgo (m/s)
  var c: [Double] = []    // steer command (normalized)
  var d: [Double] = []    // desired curvature
  var a: [Double] = []    // actual curvature
  var o: [Int] = []       // override (0/1)

  var isEmpty: Bool { t.isEmpty }

  /// Speed in km/h for charting
  var vKmh: [Double] { v.map { $0 * 3.6 } }
}
