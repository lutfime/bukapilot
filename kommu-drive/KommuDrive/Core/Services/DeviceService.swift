import Foundation
import Citadel
import Crypto

/// SSH data provider: handles all SSH communication with the device over Citadel
/// (pure-Swift NIOSSH client — no C dependencies).
///
/// Used by TuningSheet (PID editing) and DriveBrowserSheet (route analysis).
/// The embedded ed25519 keypair's public half is installed on the device via the
/// `GithubSshKeys` param; the username is `kommu`.
final class DeviceService: ObservableObject {

  /// Last error from connect/execute, surfaced to the UI so the user sees *why*
  /// a connection failed instead of a generic "failed" message.
  @Published var connectionError: String? = nil

  /// Embedded SSH keypair (dedicated, installed on device in /data/params/d/GithubSshKeys).
  private let sshKey = """
-----BEGIN OPENSSH PRIVATE KEY-----
b3BlbnNzaC1rZXktdjEAAAAABG5vbmUAAAAEbm9uZQAAAAAAAAABAAAAMwAAAAtzc2gtZW
QyNTUxOQAAACD0DFYRMlbmVNYhW1JP++w3ZDXGb85Hu661umqNOuFFvwAAAJhdMTHOXTEx
zgAAAAtzc2gtZWQyNTUxOQAAACD0DFYRMlbmVNYhW1JP++w3ZDXGb85Hu661umqNOuFFvw
AAAEC6Akvf4aucDWURnFE3hwC+Scggr5UjIup1HsH8eqQokfQMVhEyVuZU1iFbUk/77Ddk
NcZvzke7rrW6ao064UW/AAAADmtvbW11ZHJpdmUtYXBwAQIDBAUGBw==
-----END OPENSSH PRIVATE KEY-----
"""

  private let sshUser = "kommu"
  private let sshPort = 22

  private let interfacePath = "/data/safe_staging/merged/opendbc_repo/opendbc/car/proton/interface.py"
  private let lowerPath = "/data/openpilot/opendbc_repo/opendbc/car/proton/interface.py"
  private let modeldPath = "/data/safe_staging/merged/selfdrive/modeld/modeld.py"
  private let modeldLower = "/data/openpilot/selfdrive/modeld/modeld.py"

  private var client: SSHClient?

  // MARK: - IP cache (per network type, persisted to UserDefaults)

  /// Cached IPs keyed by network type: "hotspot" or the SSID.
  /// So switching between home Wi-Fi and device hotspot always gets the right IP.
  private static let cacheKey = "KDIPCache"
  private static let cacheVersionKey = "KDIPCacheVersion"
  private static let currentCacheVersion = 2  // bump to invalidate old caches

  private static var ipCache: [String: String] {
    get {
      // Invalidate cache if version changed
      let version = UserDefaults.standard.integer(forKey: cacheVersionKey)
      if version != currentCacheVersion {
        UserDefaults.standard.removeObject(forKey: cacheKey)
        UserDefaults.standard.removeObject(forKey: "KDLastDeviceIP")
        UserDefaults.standard.set(currentCacheVersion, forKey: cacheVersionKey)
        return [:]
      }
      return (UserDefaults.standard.dictionary(forKey: cacheKey) as? [String: String]) ?? [:]
    }
    set { UserDefaults.standard.set(newValue, forKey: cacheKey) }
  }

  /// Clears the entire IP cache (call when cache is stale/wrong).
  static func clearIPCache() {
    UserDefaults.standard.removeObject(forKey: cacheKey)
    UserDefaults.standard.removeObject(forKey: "KDLastDeviceIP")  // old format cleanup
  }

  /// Backwards compat — returns the cached IP for the current network.
  static var cachedIP: String? {
    let ssid = HotspotDetector.isOnDeviceHotspot ? "hotspot" : (HotspotDetector.currentSSID ?? "unknown")
    return ipCache[ssid]
  }

  /// Caches an IP for a specific network key.
  static func cacheIP(_ ip: String, forKey key: String) {
    var cache = ipCache
    cache[key] = ip
    UserDefaults.standard.set(cache, forKey: cacheKey)
  }

  /// Gets the cached IP for a specific network key.
  static func cachedIP(for key: String) -> String? {
    ipCache[key]
  }

  /// Human-readable description of all cached IPs (for debug/error messages).
  static var ipCacheDescription: String {
    let cache = ipCache
    if cache.isEmpty { return "(empty)" }
    return cache.map { "\($0.key): \($0.value)" }.joined(separator: ", ")
  }

  // MARK: - Host resolution

  /// Current network key: "hotspot" if on device hotspot, else the SSID.
  private static var networkKey: String {
    HotspotDetector.isOnDeviceHotspot ? "hotspot" : (HotspotDetector.currentSSID ?? "unknown")
  }

  /// Resolves the best host IP.
  /// Priority:
  /// 1. On hotspot → hotspot IP from BLE (or cache for "hotspot")
  /// 2. On Wi-Fi → localIP from BLE (or cache for the SSID)
  /// No fallback to other networks' IPs — that's how stale IPs get used.
  static func resolveHost(hotspotIp: String?, localIP: String?) -> String? {
    let key = networkKey

    if HotspotDetector.isOnDeviceHotspot {
      // On hotspot: BLE hotspot IP, then cache
      if let hip = hotspotIp, !hip.isEmpty { return hip }
      return cachedIP(for: key)
    } else {
      // On Wi-Fi: BLE localIP, then cache for this SSID
      if let lip = localIP, !lip.isEmpty { return lip }
      return cachedIP(for: key)
    }
  }

  /// Human-readable label for the connection target.
  static func hostLabel(ssid: String?) -> String {
    if HotspotDetector.isOnDeviceHotspot { return "device hotspot" }
    if let ssid = ssid, !ssid.isEmpty { return ssid }
    return "device"
  }

  // MARK: - Connection

  /// Connects to the device. Throws on failure — the caller surfaces the error.
  /// On success, caches the IP for the current network so reconnect is instant.
  func connect(host: String) async throws {
    connectionError = nil
    // Cache this IP for the current network (hotspot or SSID)
    Self.cacheIP(host, forKey: Self.networkKey)
    let key = try parseEd25519PrivateKey(sshKey)
    let settings = SSHClientSettings(
      host: host,
      port: sshPort,
      authenticationMethod: { [self] in SSHAuthenticationMethod.ed25519(username: sshUser, privateKey: key) },
      hostKeyValidator: .acceptAnything()
    )
    do {
      self.client = try await SSHClient.connect(to: settings)
    } catch {
      self.connectionError = describe(error)
      AppLog.error("SSH connect to \(sshUser)@\(host):\(sshPort) failed: \(error)")
      throw error
    }
  }

  /// Resolves the host and connects in one call. Returns the host that was used.
  @discardableResult
  func connectIfNeeded(hotspotIp: String?, localIP: String?) async throws -> String {
    if isConnected { return Self.cachedIP ?? "" }
    guard let host = Self.resolveHost(hotspotIp: hotspotIp, localIP: localIP) else {
      throw SSHError.notConnected
    }
    try await connect(host: host)
    return host
  }

  var isConnected: Bool { client?.isConnected ?? false }

  /// Runs a shell command on the device and returns stdout (stderr is dropped).
  /// Throws on transport failure; returns "" for commands with no output.
  func execute(_ command: String) async throws -> String {
    guard let client = client else {
      throw SSHError.notConnected
    }
    do {
      let buf = try await client.executeCommand(command)
      return String(buffer: buf)
    } catch {
      AppLog.error("SSH execute failed: \(error)")
      throw error
    }
  }

  /// Downloads a file from the device in chunks via SSH dd+base64.
  /// Chunked download avoids Citadel exec output truncation on large files.
  func downloadFile(remotePath: String, to localURL: URL,
                     progress: ((Double) -> Void)? = nil) async throws {
    guard let client = client else { throw SSHError.notConnected }

    // Get file size
    let sizeOut = try await execute("stat -c %s \(remotePath) 2>/dev/null || echo 0")
    let totalBytes = Int64(sizeOut.trimmingCharacters(in: .whitespacesAndNewlines)) ?? 0
    if totalBytes == 0 {
      throw SSHError.invalidKey("File not found or empty: \(remotePath)")
    }

    progress?(0.02)

    // Download in 512KB chunks via dd + base64 (each chunk → ~700KB base64, safe for exec)
    let chunkSize: Int64 = 512 * 1024
    var offset: Int64 = 0
    var fileData = Data()

    while offset < totalBytes {
      let toRead = min(chunkSize, totalBytes - offset)
      let cmd = "dd if=\(remotePath) bs=1 skip=\(offset) count=\(toRead) 2>/dev/null | base64 -w0"
      let b64 = try await execute(cmd)
      let cleaned = b64.trimmingCharacters(in: .whitespacesAndNewlines)
      if let chunkData = Data(base64Encoded: cleaned) {
        fileData.append(chunkData)
        offset += Int64(chunkData.count)
      } else {
        throw SSHError.invalidKey("Failed to decode chunk at offset \(offset)")
      }
      progress?(0.02 + Double(offset) / Double(totalBytes) * 0.9)
    }

    try fileData.write(to: localURL)
    progress?(1.0)
  }

  /// Finds ALL qlog paths for a route (one per segment).
  func findAllQlogPaths(route: String) async -> [String] {
    let base = "/data/media/0/realdata/\(route)"
    var paths: [String] = []
    for ext in ["zst", "bz2"] {
      let cmd = "ls -1 \(base)--*/qlog.\(ext) 2>/dev/null"
      if let out = try? await execute(cmd) {
        paths.append(contentsOf: out.split(separator: "\n").map { String($0).trimmingCharacters(in: .whitespaces) })
      }
    }
    return paths.sorted()
  }

  func disconnect() {
    client = nil
    connectionError = nil
  }

  // MARK: - Tuning (PID line editing, X70-only)

  /// A tunable line from interface.py, grouped into a section.
  /// `rawLine` includes indentation + trailing comment (what's actually in the file);
  /// `value` is just the editable part shown in the text field.
  struct TuningLine: Identifiable, Equatable {
    let lineNum: Int
    let rawLine: String        // full line with indentation
    var value: String          // just the value to edit
    let label: String          // human label
    let section: String        // "Lateral", "Longitudinal", "Steer"
    let file: String           // "interface" or "modeld" — determines which file to save to
    var id: Int { lineNum }
  }

  /// All tunable sections, each with its lines.
  struct TuningSection: Identifiable {
    var name: String
    var lines: [TuningLine]
    var id: String { name }
  }

  /// Fetches only the X70 block's tunable lines, dynamically detecting the block
  /// boundaries (not hardcoded). Preserves indentation by storing the raw line
  /// and only extracting the editable value for the UI.
  func fetchTuningLines() async -> [TuningSection] {
    // Python script on device: finds the X70 block, extracts tunable lines with
    // their raw content + line numbers, outputs JSON.
    let script = """
/usr/local/venv/bin/python3 -c "
import json
out = []

# 1. Read interface.py X70 block for PID/longitudinal/steer values
path = '\(interfacePath)'
with open(path) as f:
    lines = f.readlines()
start = None
for i, l in enumerate(lines):
    if 'candidate == CAR.PROTON_X70' in l:
        start = i
        break
if start is not None:
    end = len(lines)
    for i in range(start + 1, len(lines)):
        stripped = lines[i].rstrip()
        if stripped.startswith('    elif ') or stripped == '    else:':
            end = i
            break
    patterns = ['pid.kpBP', 'pid.kiBP', 'pid.kpV', 'pid.kiV', 'pid.kf', 'longitudinalTuning.kpBP', 'longitudinalTuning.kiBP', 'longitudinalTuning.kpV', 'longitudinalTuning.kiV', 'steerActuatorDelay =', 'steerRatio =', 'longitudinalActuatorDelay =']
    for i in range(start, end):
        raw = lines[i].rstrip('\\n')
        for p in patterns:
            if p in raw:
                val = raw.split('=', 1)[1].strip() if '=' in raw else ''
                if '#' in val:
                    val = val.split('#', 1)[0].strip()
                out.append({'line': i + 1, 'raw': raw, 'value': val, 'key': p, 'file': 'interface'})
                break

# 2. Read LAT_SMOOTH_SECONDS from modeld.py (try both paths)
for modeld in ['\(modeldPath)', '\(modeldLower)']:
    try:
        with open(modeld) as f:
            mlines = f.readlines()
        for i, l in enumerate(mlines):
            if 'LAT_SMOOTH_SECONDS =' in l and 'LAT_SMOOTH_SECONDS_011' not in l:
                raw = l.rstrip('\\n')
                val = raw.split('=', 1)[1].strip() if '=' in raw else ''
                if '#' in val:
                    val = val.split('#', 1)[0].strip()
                out.append({'line': i + 1, 'raw': raw, 'value': val, 'key': 'LAT_SMOOTH_SECONDS =', 'file': 'modeld'})
        break
    except: pass

print(json.dumps(out))
"
"""
    guard let output = try? await execute(script),
          let data = output.data(using: .utf8),
          let arr = try? JSONSerialization.jsonObject(with: data) as? [[String: Any]] else {
      return []
    }

    // Extract speed breakpoints for building informative labels
    var latKpBP = ""; var latKiBP = ""
    var longKpBP = ""; var longKiBP = ""
    for item in arr {
      guard let key = item["key"] as? String, let value = item["value"] as? String else { continue }
      if key == "pid.kpBP" { latKpBP = value }
      if key == "pid.kiBP" { latKiBP = value }
      if key == "longitudinalTuning.kpBP" { longKpBP = value }
      if key == "longitudinalTuning.kiBP" { longKiBP = value }
    }

    var lateral: [TuningLine] = []
    var longitudinal: [TuningLine] = []
    var steer: [TuningLine] = []

    for item in arr {
      guard let lineNum = item["line"] as? Int,
            let raw = item["raw"] as? String,
            let value = item["value"] as? String,
            let key = item["key"] as? String else { continue }
      let file = (item["file"] as? String) ?? "interface"
      // Skip the breakpoint lines themselves (not editable values, just context)
      if key == "pid.kpBP" || key == "pid.kiBP" || key == "longitudinalTuning.kpBP" || key == "longitudinalTuning.kiBP" { continue }
      let (label, section) = Self.classify(key)
      // Build speed-aware label for gain arrays
      let speedLabel = Self.speedLabel(key, latKpBP: latKpBP, latKiBP: latKiBP, longKpBP: longKpBP, longKiBP: longKiBP)
      let fullLabel = speedLabel.isEmpty ? label : "\(label) \(speedLabel)"
      let tl = TuningLine(lineNum: lineNum, rawLine: raw, value: value, label: fullLabel, section: section, file: file)
      switch section {
      case "Lateral":      lateral.append(tl)
      case "Longitudinal": longitudinal.append(tl)
      default:             steer.append(tl)
      }
    }
    var sections: [TuningSection] = []
    if !steer.isEmpty { sections.append(TuningSection(name: "Steer", lines: steer)) }
    if !lateral.isEmpty { sections.append(TuningSection(name: "Lateral (PID)", lines: lateral)) }
    if !longitudinal.isEmpty { sections.append(TuningSection(name: "Longitudinal", lines: longitudinal)) }
    return sections
  }

  private static func classify(_ key: String) -> (label: String, section: String) {
    if key.contains("LAT_SMOOTH_SECONDS")        { return ("LAT_SMOOTH_SECONDS (s)", "Lateral") }
    if key.contains("steerRatio")                { return ("steerRatio", "Steer") }
    if key.contains("steerActuatorDelay")        { return ("steerActuatorDelay (s)", "Steer") }
    if key.contains("longitudinalActuatorDelay") { return ("longitudinalActuatorDelay (s)", "Longitudinal") }
    if key.contains("longitudinalTuning.kpV")    { return ("kpV (proportional)", "Longitudinal") }
    if key.contains("longitudinalTuning.kiV")    { return ("kiV (integral)", "Longitudinal") }
    if key.contains("pid.kpV") { return ("kpV (proportional gain)", "Lateral") }
    if key.contains("pid.kiV") { return ("kiV (integral gain)", "Lateral") }
    if key.contains("pid.kf")  { return ("kf (feed-forward)", "Lateral") }
    return (key, "Steer")
  }

  /// Converts the speed breakpoint arrays into a human-readable speed string.
  /// Works for both lateral (pid.kpBP/kiBP) and longitudinal (longitudinalTuning.kpBP/kiBP).
  private static func speedLabel(_ key: String, latKpBP: String, latKiBP: String, longKpBP: String, longKiBP: String) -> String {
    let bpStr: String
    if key.contains("pid.kpV") { bpStr = latKpBP }
    else if key.contains("pid.kiV") { bpStr = latKiBP }
    else if key.contains("longitudinalTuning.kpV") { bpStr = longKpBP }
    else if key.contains("longitudinalTuning.kiV") { bpStr = longKiBP }
    else { return "" }

    // Parse "[0.0, 5.0, 15.0, 25.0, 35.0]" → [0, 18, 54, 90, 126] km/h
    let cleaned = bpStr.trimmingCharacters(in: CharacterSet(charactersIn: "[] "))
    let parts = cleaned.split(separator: ",")
    let kmh = parts.compactMap { Double($0.trimmingCharacters(in: .whitespaces)) }.map { Int($0 * 3.6) }
    if kmh.isEmpty { return "" }
    return "@ \(kmh.map(String.init).joined(separator: "/")) km/h"
  }

  /// Saves a value by rewriting only the value part of the line on the device,
  /// preserving indentation, the key, and any trailing comment. CRITICAL: the
  /// file is validated IN MEMORY before writing — if py_compile fails, the file
  /// is never touched. No corruption is possible.
  func saveTuningLine(lineNum: Int, rawLine: String, newValue: String, file: String = "interface") async -> (ok: Bool, detail: String) {
    let valB64 = Data(newValue.utf8).base64EncodedString()
    let path = file == "modeld" ? modeldPath : interfacePath
    let lower = file == "modeld" ? modeldLower : lowerPath
    let script = """
/usr/local/venv/bin/python3 -c "
import base64
path = '\(path)'
lower = '\(lower)'
line_num = \(lineNum)
new_val = base64.b64decode('\(valB64)').decode('utf-8')

# Read the file
with open(path) as f:
    lines = f.readlines()

idx = line_num - 1
old_line = lines[idx]

# Build the new line IN MEMORY (don't write to file yet)
if '=' in old_line:
    before_eq = old_line.split('=', 1)[0].rstrip()
    after_eq = old_line.split('=', 1)[1]
    comment = ''
    if '#' in after_eq:
        comment = '  #' + after_eq.split('#', 1)[1].rstrip()
    new_line = before_eq + ' = ' + new_val + comment + '\\n'
else:
    new_line = old_line

# Create a modified copy IN MEMORY for validation
test_lines = list(lines)
test_lines[idx] = new_line
test_source = ''.join(test_lines)

# Validate in memory — compile the modified source WITHOUT touching the file.
# If this fails, the file is NEVER written. Zero corruption risk.
try:
    compile(test_source, path, 'exec')
except SyntaxError as e:
    print('__KD_FAIL__ Syntax error: ' + str(e))
    exit()

# Validation passed — NOW write to both files
lines[idx] = new_line
with open(path, 'w') as f:
    f.writelines(lines)

try:
    with open(lower) as f2:
        lines2 = f2.readlines()
    lines2[idx] = new_line
    with open(lower, 'w') as f2:
        f2.writelines(lines2)
except: pass

print('__KD_OK__')
"
"""
    let result = (try? await execute(script)) ?? "__KD_FAIL__ (no response from device)"
    if result.contains("__KD_OK__") {
      return (true, "Saved")
    }
    let clean = result
      .replacingOccurrences(of: "__KD_OK__", with: "")
      .replacingOccurrences(of: "__KD_FAIL__", with: "")
      .trimmingCharacters(in: .whitespacesAndNewlines)
    return (false, clean.isEmpty ? "Failed (no detail)" : clean)
  }

  // MARK: - Map corner slowdown (params get/put over SSH)

  /// Reads the map-corner-slowdown params from the device. Returns nil values on failure.
  /// Runs a Python script in the openpilot venv (Params is the canonical reader).
  struct MapCornerParams: Equatable {
    var enabled: Bool
    var budget: Double      // m/s^2, 1.0–4.0
    var lookahead: Double   // meters
    var valid: Bool         // MapCornerValid (live state)
    var vCorner: Double     // m/s (live state, current advisory)
  }

  func fetchMapCornerParams() async -> MapCornerParams? {
    // The script is base64-encoded so there are zero shell-quoting issues.
    let script = """
import sys; sys.path.insert(0, '/data/openpilot')
from openpilot.common.params import Params
p = Params()
def g(k, d=''):
    v = p.get(k)
    return v if isinstance(v, str) else (d if v is None else str(v))
print('enabled=' + g('MapCornerEnabled', '0'))
print('budget=' + g('MapCornerBudget', '2.5'))
print('lookahead=' + g('MapCornerLookahead', '200'))
print('valid=' + g('MapCornerValid', '0'))
print('vcorner=' + g('vCruiseMapCorner', '0'))
"""
    let b64 = Data(script.utf8).base64EncodedString()
    let cmd = "/usr/local/venv/bin/python3 -c \"import base64; exec(base64.b64decode('\(b64)'))\""
    do {
      let out = try await execute(cmd)
      var d: [String: String] = [:]
      for line in out.split(separator: "\n") {
        let parts = line.split(separator: "=", maxSplits: 1)
        if parts.count == 2 { d[String(parts[0])] = String(parts[1]) }
      }
      return MapCornerParams(
        enabled: d["enabled"] == "1" || d["enabled"] == "True" || d["enabled"] == "true",
        budget: Double(d["budget"] ?? "2.5") ?? 2.5,
        lookahead: Double(d["lookahead"] ?? "200") ?? 200.0,
        valid: d["valid"] == "1" || d["valid"] == "True" || d["valid"] == "true",
        vCorner: Double(d["vcorner"] ?? "0") ?? 0.0
      )
    } catch {
      AppLog.error("fetchMapCornerParams failed: \(error)")
      return nil
    }
  }

  /// Writes a single map-corner param. `key` is one of MapCornerEnabled / MapCornerBudget / MapCornerLookahead.
  /// Values arrive as strings ("0"/"1" for the enable bool, "2.5"/"200" for floats), and are dispatched to the
  /// correct typed Params call — the device's strict params cast table rejects a str for BOOL/FLOAT keys.
  func saveMapCornerParam(key: String, value: String) async -> (ok: Bool, detail: String) {
    // Both key and value are base64-encoded to avoid any shell-quoting issues.
    let script = """
import sys, base64; sys.path.insert(0, '/data/openpilot')
from openpilot.common.params import Params
k = base64.b64decode('__KEY__').decode()
v = base64.b64decode('__VAL__').decode()
p = Params()
if k == 'MapCornerEnabled':
    p.put_bool(k, v.lower() in ('1', 'true'))
else:
    p.put(k, float(v))
print('__KD_OK__')
"""
    let k64 = Data(key.utf8).base64EncodedString()
    let v64 = Data(value.utf8).base64EncodedString()
    let filled = script.replacingOccurrences(of: "__KEY__", with: k64)
                        .replacingOccurrences(of: "__VAL__", with: v64)
    let b64 = Data(filled.utf8).base64EncodedString()
    let cmd = "/usr/local/venv/bin/python3 -c \"import base64; exec(base64.b64decode('\(b64)'))\""
    do {
      let out = try await execute(cmd)
      let clean = out.trimmingCharacters(in: .whitespacesAndNewlines)
      if clean.contains("__KD_OK__") {
        return (true, "Saved \(key)=\(value)")
      }
      return (false, clean.isEmpty ? "Failed (no output)" : clean)
    } catch {
      return (false, "SSH error: \(error)")
    }
  }

  // MARK: - Route browsing

  /// A drive: the base route name (without segment number) + how many segments.
  struct Drive: Identifiable, Equatable {
    let route: String        // e.g. "2026-08-08--03-55-22--7" with --N stripped
    let segmentCount: Int
    let firstSegmentDir: String   // the actual dir name to read qlog from
    var id: String { route }

    /// Creates a display ViewModel for this drive.
    var routeVM: DriveRouteVM {
      DriveRouteVM(rawRoute: route, segmentCount: segmentCount)
    }
  }

  /// Lists drives, grouping segments that share a base route name.
  /// Segment dirs look like `<route>--0`, `<route>--1`, ... — we collapse them.
  func fetchRoutes() async -> [Drive] {
    guard let output = try? await execute("ls -1 /data/media/0/realdata/ | grep -v boot | sort -r") else { return [] }
    // Group by base name (everything before the final --<number>)
    var groups: [String: [String]] = [:]
    for seg in output.split(separator: "\n") {
      let s = String(seg)
      // Strip the trailing --N segment index
      if let dash = s.range(of: "--", options: .backwards) {
        // only treat as segment if the part after last -- is all digits
        let suffix = s[dash.upperBound...]
        if suffix.allSatisfy(\.isNumber), !suffix.isEmpty {
          let base = String(s[..<dash.lowerBound])
          groups[base, default: []].append(s)
          continue
        }
      }
      // No segment suffix — single-segment route
      groups[s, default: []].append(s)
    }
    // Build Drive list, newest first (sort by the dir's name which starts with a date)
    return groups.keys.sorted().reversed().map { base in
      let segs = groups[base] ?? []
      // firstSegmentDir = the lowest-numbered segment (segment 0 if present)
      let sorted = segs.sorted { a, b in
        segIndex(a) < segIndex(b)
      }
      return Drive(route: base, segmentCount: max(segs.count, 1), firstSegmentDir: sorted.first ?? base)
    }
  }

  /// Extracts the segment index from a dir name like `route--3` → 3.
  private func segIndex(_ dir: String) -> Int {
    guard let dash = dir.range(of: "--", options: .backwards) else { return 0 }
    let suffix = dir[dash.upperBound...]
    return Int(suffix) ?? 0
  }

  // MARK: - Drive data cache (disk)

  /// Cache directory for parsed drive JSON files.
  static var cacheDir: URL {
    let docs = FileManager.default.urls(for: .documentDirectory, in: .userDomainMask)[0]
    let dir = docs.appendingPathComponent("DriveCache", isDirectory: true)
    try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)

    // Invalidate old cache versions
    let versionKey = "KDDriveCacheVersion"
    let currentVersion = 2
    if UserDefaults.standard.integer(forKey: versionKey) != currentVersion {
      try? FileManager.default.removeItem(at: dir)
      try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
      UserDefaults.standard.set(currentVersion, forKey: versionKey)
    }

    return dir
  }

  /// Total size of the drive cache in bytes.
  static var cacheSizeBytes: Int {
    guard let files = try? FileManager.default.contentsOfDirectory(at: cacheDir, includingPropertiesForKeys: [.fileSizeKey]) else { return 0 }
    return files.reduce(0) { $0 + ((try? $1.resourceValues(forKeys: [.fileSizeKey]).fileSize) ?? 0) }
  }

  /// Human-readable cache size (e.g. "2.3 MB").
  static var cacheSizeString: String {
    let b = cacheSizeBytes
    if b > 1_000_000 { return String(format: "%.1f MB", Double(b) / 1_000_000) }
    if b > 1_000 { return String(format: "%.0f KB", Double(b) / 1_000) }
    return "\(b) B"
  }

  /// Number of cached drives.
  static var cacheCount: Int {
    (try? FileManager.default.contentsOfDirectory(at: cacheDir, includingPropertiesForKeys: nil))?.count ?? 0
  }

  /// Clears all cached drive data.
  static func clearCache() {
    try? FileManager.default.removeItem(at: cacheDir)
    try? FileManager.default.createDirectory(at: cacheDir, withIntermediateDirectories: true)
  }

  /// Cache file path for a route.
  private func cacheFile(for route: String) -> URL {
    Self.cacheDir.appendingPathComponent("\(route).json")
  }

  /// Lists all cached drives (from the DriveCache directory).
  /// Works offline — no SSH needed.
  static func cachedDrives() -> [Drive] {
    guard let entries = try? FileManager.default.contentsOfDirectory(at: cacheDir, includingPropertiesForKeys: nil) else {
      return []
    }
    // JSON files = parsed drive data, route dirs = raw qlog downloads
    var routes = Set<String>()
    for entry in entries {
      let name = entry.lastPathComponent
      if name.hasSuffix(".json") {
        let route = String(name.dropLast(5))
        routes.insert(route)
      } else if entry.hasDirectoryPath {
        // It's a qlog directory — also a cached drive
        routes.insert(name)
      }
    }
    return routes.sorted().reversed().map { route in
      // Count segments from the qlog dir if present
      let dir = cacheDir.appendingPathComponent(route, isDirectory: true)
      let segCount = (try? FileManager.default.contentsOfDirectory(atPath: dir.path))?.count ?? 0
      return Drive(route: route, segmentCount: segCount, firstSegmentDir: route)
    }
  }

  /// Loads cached drive data if available.
  func loadCachedDriveData(route: String) -> DriveData? {
    let file = cacheFile(for: route)
    guard let data = try? Data(contentsOf: file) else { return nil }
    return try? JSONDecoder().decode(DriveData.self, from: data)
  }

  /// Saves drive data to cache for future re-use.
  func saveDriveDataToCache(_ data: DriveData, route: String) {
    let file = cacheFile(for: route)
    if let encoded = try? JSONEncoder().encode(data) {
      try? encoded.write(to: file)
    }
  }

  /// Fetches drive data: downloads the qlog file (with progress), then parses it
  /// on the device (fast — segment 0 only, ~10Hz qlog). Both raw qlog and parsed
  /// JSON are cached on disk. Re-opening is instant.
  func fetchDriveData(route: String, progress: ((Double, String) -> Void)? = nil) async -> (data: DriveData?, error: String?) {
    // 1. Check parsed cache first — instant return.
    if let cached = loadCachedDriveData(route: route), !cached.isEmpty {
      progress?(1.0, "Loaded from cache")
      return (cached, nil)
    }

    // 2. Find ALL qlog paths (one per segment)
    progress?(0.0, "Finding qlogs…")
    let qlogPaths = await findAllQlogPaths(route: route)
    if qlogPaths.isEmpty {
      return (nil, "No qlogs found for this route on the device.")
    }

    // 3. Download ALL raw qlog files to the phone (for sharing + caching)
    let localDir = Self.cacheDir.appendingPathComponent(route, isDirectory: true)
    try? FileManager.default.createDirectory(at: localDir, withIntermediateDirectories: true)

    let totalSegments = qlogPaths.count
    for (i, qlogPath) in qlogPaths.enumerated() {
      let ext = qlogPath.hasSuffix(".bz2") ? "bz2" : "zst"
      let localFile = localDir.appendingPathComponent("qlog-\(i).\(ext)")
      if !FileManager.default.fileExists(atPath: localFile.path) {
        let pct = Double(i) / Double(totalSegments)
        progress?(pct * 0.7, "Downloading segment \(i+1)/\(totalSegments)…")
        do {
          try await downloadFile(remotePath: qlogPath, to: localFile)
        } catch {
          return (nil, "Download failed (segment \(i+1)): \(error.localizedDescription)")
        }
      }
    }
    progress?(0.75, "Downloaded \(totalSegments) segment\(totalSegments == 1 ? "" : "s")")

    // 4. Parse ALL qlogs LOCALLY (native Swift, no device Python needed)
    progress?(0.8, "Parsing signals…")
    var combined = DriveData()
    let routeStart = parseRouteStartTime(route)

    for (i, qlogPath) in qlogPaths.enumerated() {
      let ext = qlogPath.hasSuffix(".bz2") ? "bz2" : "zst"
      let localFile = localDir.appendingPathComponent("qlog-\(i).\(ext)")
      guard FileManager.default.fileExists(atPath: localFile.path),
            let qlogData = try? Data(contentsOf: localFile) else {
        continue
      }
      let parsed = QlogParser.parse(qlogData, routeStartTime: i == 0 ? routeStart : 0)
      combined.t.append(contentsOf: parsed.t)
      combined.v.append(contentsOf: parsed.v)
      combined.c.append(contentsOf: parsed.c)
      combined.d.append(contentsOf: parsed.d)
      combined.a.append(contentsOf: parsed.a)
      combined.o.append(contentsOf: parsed.o)
      combined.p.append(contentsOf: parsed.p)
      combined.i.append(contentsOf: parsed.i)
      combined.f.append(contentsOf: parsed.f)
      combined.sat.append(contentsOf: parsed.sat)
      combined.eng.append(contentsOf: parsed.eng)
      combined.trq.append(contentsOf: parsed.trq)
      let pct = 0.8 + (Double(i + 1) / Double(totalSegments)) * 0.2
      progress?(pct, "Parsed segment \(i+1)/\(totalSegments)…")
    }

    if combined.isEmpty {
      return (nil, "No controlsState events found in any qlog.")
    }

    progress?(1.0, "Done")
    saveDriveDataToCache(combined, route: route)
    return (combined, nil)
  }

  /// Parses the route start time from the route name (UTC).
  private func parseRouteStartTime(_ route: String) -> Double {
    let parts = route.split(separator: "--")
    guard parts.count >= 2 else { return 0 }
    let dateStr = "\(parts[0]) \(parts[1])"
    let df = DateFormatter()
    df.dateFormat = "yyyy-MM-dd HH-mm-ss"
    df.timeZone = TimeZone(identifier: "UTC")
    return df.date(from: dateStr)?.timeIntervalSince1970 ?? 0
  }

  /// Runs the parse script on device, parsing ALL qlog segments.
  private func parseQlogsOnDevice(route: String, qlogPaths: [String]) async -> (data: DriveData?, error: String?) {
    // Pass the qlog paths as a JSON array (base64 to avoid quoting issues)
    let pathsJSON = (try? JSONEncoder().encode(qlogPaths)).flatMap { String(data: $0, encoding: .utf8) } ?? "[]"
    let pathsB64 = Data(pathsJSON.utf8).base64EncodedString()

    let script = """
cat > /tmp/kd_parse.py <<'PYEOF'
import json, zstandard, bz2, sys, traceback, calendar, time, base64
try:
    from cereal import log as capnp_log
    EV = capnp_log.Event
    route_name = '\(route)'
    qlog_paths = json.loads(base64.b64decode('\(pathsB64)').decode('utf-8'))
    try:
        parts = route_name.split('--')
        dp = parts[0] + ' ' + parts[1]
        route_start = calendar.timegm(time.strptime(dp, '%Y-%m-%d %H-%M-%S'))
    except:
        route_start = 0

    def decompress(p):
        if p.endswith('.zst'):
            dctx = zstandard.ZstdDecompressor()
            with open(p, 'rb') as f: return dctx.stream_reader(f).read()
        else:
            with open(p, 'rb') as f: return bz2.decompress(f.read())

    data = {'t':[], 'v':[], 'c':[], 'd':[], 'a':[], 'o':[], 'p':[], 'i':[], 'f':[], 'sat':[], 'eng':[], 'trq':[]}
    first_mono = None
    last_v = 0.0; last_o = 0; last_trq = 0.0

    for path in qlog_paths:
        try:
            events = list(EV.read_multiple_bytes(decompress(path)))
            for e in events:
                try: w = e.which()
                except: continue
                mono = e.logMonoTime / 1e9
                if first_mono is None: first_mono = mono
                t_wall = route_start + (mono - first_mono)
                if w == 'carState':
                    try: last_v = round(float(e.carState.vEgo), 2)
                    except: pass
                    try: last_o = 1 if e.carState.steeringPressed else 0
                    except: pass
                    try: last_trq = round(float(getattr(e.carState, 'steeringTorque', 0)), 1)
                    except: pass
                elif w == 'controlsState':
                    cs = e.controlsState
                    data['t'].append(round(t_wall, 2))
                    data['v'].append(last_v); data['o'].append(last_o); data['trq'].append(last_trq)
                    try: data['d'].append(round(float(cs.desiredCurvature), 5))
                    except: data['d'].append(0)
                    try: data['a'].append(round(float(cs.curvature), 5))
                    except: data['a'].append(0)
                    try: data['eng'].append(1 if cs.enabled else 0)
                    except: data['eng'].append(0)
                    try:
                        pid = cs.lateralControlState.pidState
                        data['c'].append(round(float(pid.output), 4))
                        try: data['p'].append(round(float(getattr(pid, 'p', 0)), 4))
                        except: data['p'].append(0)
                        try: data['i'].append(round(float(getattr(pid, 'i', 0)), 4))
                        except: data['i'].append(0)
                        try: data['f'].append(round(float(getattr(pid, 'f', 0)), 4))
                        except: data['f'].append(0)
                        try: data['sat'].append(1 if getattr(pid, 'saturated', False) else 0)
                        except: data['sat'].append(0)
                    except:
                        data['c'].append(0); data['p'].append(0); data['i'].append(0); data['f'].append(0); data['sat'].append(0)
        except: pass

    if not data['t']:
        print(json.dumps({'_error': 'No controlsState events found in any segment.'}))
    else:
        print(json.dumps(data))
except Exception:
    print(json.dumps({'_error': traceback.format_exc()[-500:]}))
PYEOF
cd /data/openpilot && PYTHONPATH=/data/openpilot /usr/local/venv/bin/python /tmp/kd_parse.py; rm -f /tmp/kd_parse.py
"""
    // Execute the parse script
    let output: String
    do {
      output = try await execute(script)
    } catch {
      return (nil, "SSH execute failed: \(error.localizedDescription)")
    }

    let trimmed = output.trimmingCharacters(in: .whitespacesAndNewlines)
    if trimmed.isEmpty {
      return (nil, "Device returned empty output. The Python script may have crashed.")
    }
    guard let jsonData = output.data(using: .utf8) else {
      return (nil, "Invalid response encoding")
    }
    if let dict = try? JSONSerialization.jsonObject(with: jsonData) as? [String: Any],
       let errMsg = dict["_error"] as? String {
      return (nil, errMsg)
    }
    do {
      let parsed = try JSONDecoder().decode(DriveData.self, from: jsonData)
      AppLog.info("parseQlogs: \(parsed.t.count) points, \(qlogPaths.count) segments")
      return (parsed, nil)
    } catch {
      let preview = String(output.prefix(500))
      return (nil, "Decode failed: \(error.localizedDescription)\n\nRaw output:\n\(preview)")
    }
  }

  // MARK: - OpenSSH ed25519 key parsing

  /// Parses an OpenSSH-format ed25519 private key (RFC 8032) into a CryptoKit
  /// `Curve25519.Signing.PrivateKey`. The OpenSSH private blob is 64 bytes =
  /// `seed (32) || public (32)`; CryptoKit takes the 32-byte seed directly.
  ///
  /// Only supports unencrypted ed25519 keys (cipher=none), which is all we embed.
  private func parseEd25519PrivateKey(_ pem: String) throws -> Curve25519.Signing.PrivateKey {
    // Strip PEM armor + join base64 body
    let body = pem
      .split(separator: "\n")
      .filter { !$0.contains("BEGIN") && !$0.contains("END") }
      .joined()
    guard let blob = Data(base64Encoded: body) else {
      throw SSHError.invalidKey("not valid base64")
    }

    // OpenSSH key format (https://github.com/openssh/openssh-portable/blob/master/PROTOCOL.key):
    //   "openssh-key-v1\0"          (magic, NUL-terminated ASCII)
    //   string ciphername            ("none" for unencrypted)
    //   string kdfname               ("none")
    //   string kdfoptions            ("")
    //   uint32 number of keys (N)    (1)
    //   publickey[N]
    //   encrypted section (plaintext when cipher=none)

    var off = 0
    func readU32() throws -> UInt32 {
      guard off + 4 <= blob.count else { throw SSHError.invalidKey("truncated u32") }
      let v = blob.subdata(in: off..<(off + 4)).withUnsafeBytes { $0.load(as: UInt32.self).bigEndian }
      off += 4
      return v
    }
    func readString() throws -> Data {
      let len = Int(try readU32())
      guard off + len <= blob.count, len >= 0 else { throw SSHError.invalidKey("truncated string") }
      let s = blob.subdata(in: off..<(off + len))
      off += len
      return s
    }

    // magic: NUL-terminated ASCII, not length-prefixed
    guard let nul = blob.firstIndex(of: 0) else { throw SSHError.invalidKey("missing magic terminator") }
    let magic = blob.subdata(in: 0..<nul)
    off = nul + 1
    guard magic == Data("openssh-key-v1".utf8) else {
      throw SSHError.invalidKey("bad magic \(magic)")
    }

    let ciphername = try readString()
    guard ciphername == Data("none".utf8) else {
      throw SSHError.invalidKey("encrypted keys not supported (cipher=\(String(data: ciphername, encoding: .utf8) ?? "?"))")
    }
    _ = try readString()   // kdfname ("none")
    _ = try readString()   // kdfoptions ("")
    let nkeys = try readU32()
    guard nkeys == 1 else { throw SSHError.invalidKey("expected 1 key, got \(nkeys)") }

    _ = try readString()   // public key blob (also present inside private section)
    let privSection = try readString()

    // Private section (plaintext since cipher=none):
    //   uint32 checkint, uint32 checkint (must match)
    //   string keytype ("ssh-ed25519")
    //   string public  (32)
    //   string private (64 = seed || pub)
    //   string comment
    //   padding
    var p = 0
    func pU32() throws -> UInt32 {
      guard p + 4 <= privSection.count else { throw SSHError.invalidKey("priv: truncated u32") }
      let v = privSection.subdata(in: p..<(p + 4)).withUnsafeBytes { $0.load(as: UInt32.self).bigEndian }
      p += 4
      return v
    }
    func pString() throws -> Data {
      let len = Int(try pU32())
      guard p + len <= privSection.count, len >= 0 else { throw SSHError.invalidKey("priv: truncated string") }
      let s = privSection.subdata(in: p..<(p + len))
      p += len
      return s
    }

    let c1 = try pU32(), c2 = try pU32()
    guard c1 == c2 else { throw SSHError.invalidKey("checkints don't match") }

    let keytype = try pString()
    guard keytype == Data("ssh-ed25519".utf8) else {
      throw SSHError.invalidKey("not an ed25519 key (type=\(String(data: keytype, encoding: .utf8) ?? "?"))")
    }
    _ = try pString()                 // public (32) — redundant with blob
    let privBlob = try pString()      // 64 bytes: seed(32) || pub(32)
    guard privBlob.count == 64 else {
      throw SSHError.invalidKey("ed25519 priv blob is \(privBlob.count) bytes, expected 64")
    }

    let seed = privBlob.prefix(32)
    return try Curve25519.Signing.PrivateKey(rawRepresentation: seed)
  }

  /// Turns an opaque thrown error into a short human-readable string for the UI.
  private func describe(_ error: Error) -> String {
    let msg = String(describing: error)
    // NIOSSH/Citadel errors are verbose; pull out the useful leading phrase.
    if msg.contains("authentication") || msg.lowercased().contains("auth") {
      return "Authentication rejected (key not installed on device?). \(msg)"
    }
    if msg.contains("connection refused") || msg.contains("reset") {
      return "Connection refused/reset — is SSH enabled on the device? \(msg)"
    }
    if msg.contains("timed out") || msg.lowercased().contains("timeout") {
      return "Connection timed out — wrong IP, or device unreachable on Wi-Fi. \(msg)"
    }
    if msg.contains("resolve") || msg.contains("unknown host") {
      return "Could not resolve host \(msg)"
    }
    return msg
  }
}

enum SSHError: LocalizedError {
  case notConnected
  case invalidKey(String)

  var errorDescription: String? {
    switch self {
    case .notConnected: return "SSH not connected"
    case .invalidKey(let why): return "Invalid SSH key: \(why)"
    }
  }
}

/// Parsed drive data (from the device's qlog via SSH).
struct DriveData: Codable {
  var t: [Double] = []    // timestamps (epoch seconds)
  var v: [Double] = []    // vEgo (m/s)
  var c: [Double] = []    // steer command (PID output, normalized)
  var d: [Double] = []    // desired curvature
  var a: [Double] = []    // actual curvature
  var o: [Int] = []       // override (0/1)
  var p: [Double] = []    // PID P term
  var i: [Double] = []    // PID I term
  var f: [Double] = []    // PID F (feedforward) term
  var sat: [Int] = []     // PID saturated (0/1)
  var eng: [Int] = []     // openpilot enabled (0/1)
  var trq: [Double] = []  // steering torque (Nm)

  var isEmpty: Bool { t.isEmpty }

  /// Speed in km/h for charting
  var vKmh: [Double] { v.map { $0 * 3.6 } }

  /// Downsamples all arrays to at most `maxPoints` evenly-spaced samples.
  /// Keeps charts fast — rendering 2000+ LineMarks on iOS is unusably laggy.
  func downsampled(maxPoints: Int = 200) -> DownsampledData {
    guard t.count > maxPoints else {
      return DownsampledData(t: t, v: v, c: c, d: d, a: a, o: o, p: p, i: i, f: f, sat: sat, eng: eng, trq: trq)
    }
    let stride = max(1, t.count / maxPoints)
    var dsT: [Double] = [], dsV: [Double] = [], dsC: [Double] = []
    var dsD: [Double] = [], dsA: [Double] = [], dsO: [Int] = []
    var dsP: [Double] = [], dsI: [Double] = [], dsF: [Double] = []
    var dsSat: [Int] = [], dsEng: [Int] = []
    var dsTrq: [Double] = []
    for idx in Swift.stride(from: 0, to: t.count, by: stride) {
      dsT.append(t[idx])
      dsV.append(v[idx])
      dsC.append(c[idx])
      dsD.append(d[idx])
      dsA.append(a[idx])
      dsO.append(o[idx])
      dsP.append(idx < p.count ? p[idx] : 0)
      dsI.append(idx < i.count ? i[idx] : 0)
      dsF.append(idx < f.count ? f[idx] : 0)
      dsSat.append(idx < sat.count ? sat[idx] : 0)
      dsEng.append(idx < eng.count ? eng[idx] : 0)
      dsTrq.append(idx < trq.count ? trq[idx] : 0)
    }
    return DownsampledData(t: dsT, v: dsV, c: dsC, d: dsD, a: dsA, o: dsO, p: dsP, i: dsI, f: dsF, sat: dsSat, eng: dsEng, trq: dsTrq)
  }
}

/// Downsampled, display-ready drive data with precomputed summary stats.
struct DownsampledData {
  let t: [Double]   // raw epoch timestamps (seconds)
  let v: [Double]   // speed m/s
  let c: [Double]   // steer command
  let d: [Double]   // desired curvature
  let a: [Double]   // actual curvature
  let o: [Int]      // override
  let p: [Double]   // PID P term
  let i: [Double]   // PID I term
  let f: [Double]   // PID F (feedforward) term
  let sat: [Int]    // PID saturated (0/1)
  let eng: [Int]    // openpilot enabled (0/1)
  let trq: [Double] // steering torque

  // All summary stats are precomputed (let), not computed on every render.
  let duration: Double
  let durationString: String
  let avgSpeed: Double
  let avgSpeedString: String
  let maxSpeed: Double
  let maxSpeedString: String
  let avgStep: Double
  let maxStep: Double
  let overrides: Int
  let tuningGuidance: String

  init(t: [Double], v: [Double], c: [Double], d: [Double], a: [Double], o: [Int],
       p: [Double], i: [Double], f: [Double], sat: [Int], eng: [Int], trq: [Double]) {
    self.t = t; self.v = v; self.c = c; self.d = d; self.a = a; self.o = o
    self.p = p; self.i = i; self.f = f; self.sat = sat; self.eng = eng; self.trq = trq

    duration = (t.last ?? 0) - (t.first ?? 0)
    let m = Int(duration) / 60
    let s = Int(duration) % 60
    durationString = String(format: "%d:%02d", m, s)

    avgSpeed = v.isEmpty ? 0 : v.reduce(0, +) / Double(v.count) * 3.6
    avgSpeedString = String(format: "%.0f km/h", avgSpeed)
    maxSpeed = (v.map { $0 * 3.6 }.max()) ?? 0
    maxSpeedString = String(format: "%.0f km/h", maxSpeed)

    if c.count > 1 {
      var total = 0.0
      for idx in 1..<c.count { total += abs(c[idx] - c[idx-1]) }
      avgStep = total / Double(c.count - 1)
      var mx = 0.0
      for idx in 1..<c.count { mx = Swift.max(mx, abs(c[idx] - c[idx-1])) }
      maxStep = mx
    } else {
      avgStep = 0; maxStep = 0
    }
    overrides = o.filter { $0 == 1 }.count

    var lines: [String] = []
    if avgStep > 0.05 {
      lines.append("• Steer steps are large (avg \(String(format: "%.3f", avgStep))). Increase LAT_SMOOTH_SECONDS (0.15→0.2) to smooth the model's desired curvature before the PID sees it.")
    } else {
      lines.append("• Steer steps look smooth (avg \(String(format: "%.3f", avgStep))). No jerky-feel issue detected.")
    }
    if maxStep > 0.3 {
      lines.append("• Max step is \(String(format: "%.2f", maxStep)) — occasional big jump. Usually a corner entry. Normal unless frequent.")
    }
    if overrides > c.count / 10 && c.count > 20 {
      lines.append("• High override count (\(overrides)). Driver intervened often — may indicate uncomfortable steering. Check if kpV is too high.")
    }
    if lines.isEmpty { lines.append("• Data looks healthy. No obvious tuning changes needed.") }
    tuningGuidance = lines.joined(separator: "\n")
  }
}
