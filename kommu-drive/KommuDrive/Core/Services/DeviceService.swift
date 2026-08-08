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

  // MARK: - Cached device IP (survives app restart, no BLE wait needed)

  private static let ipKey = "KDLastDeviceIP"

  /// The last known device IP. Persisted to UserDefaults so SSH reconnects
  /// instantly on app reopen — no need to wait for the BLE settings frame.
  static var cachedIP: String? {
    get { UserDefaults.standard.string(forKey: ipKey) }
    set {
      if let ip = newValue, !ip.isEmpty {
        UserDefaults.standard.set(ip, forKey: ipKey)
      } else {
        UserDefaults.standard.removeObject(forKey: ipKey)
      }
    }
  }

  /// Convenience: use the cached IP if available, else nil.
  var lastKnownIP: String? { Self.cachedIP }

  // MARK: - Connection

  /// Connects to the device. Throws on failure — the caller surfaces the error.
  /// On success, caches the IP for instant reconnect next time.
  func connect(host: String) async throws {
    connectionError = nil
    Self.cachedIP = host    // cache even before connecting so retry is instant
    connectionError = nil
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

# 2. Read LAT_SMOOTH_SECONDS from modeld.py (separate file, same section concept)
modeld = '\(modeldPath)'
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

  // MARK: - Route browsing

  /// A drive: the base route name (without segment number) + how many segments.
  struct Drive: Identifiable, Equatable {
    let route: String        // e.g. "2026-08-08--03-55-22--7" with --N stripped
    let segmentCount: Int
    let firstSegmentDir: String   // the actual dir name to read qlog from
    var id: String { route }
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

  /// Fetches drive data by parsing qlogs on the device. Merges ALL segments of
  /// a drive (not just segment 0) so multi-segment drives show complete data.
  /// Returns (data, error) so the UI can show the actual failure reason.
  func fetchDriveData(route: String) async -> (data: DriveData?, error: String?) {
    // Write the Python script to a temp file on the device to avoid shell quoting hell,
    // then run it. Wraps everything in try/except so errors come back as JSON, not crashes.
    let script = """
cat > /tmp/kd_parse.py <<'PYEOF'
import json, zstandard, glob, os, bz2, sys, traceback
try:
    from cereal import log as capnp_log
    EV = capnp_log.Event
    route_name = '\(route)'
    base = '/data/media/0/realdata/' + route_name

    # Parse the route name to get the wall-clock start time (UTC).
    # Route format: "2026-08-08--03-55-22" → Unix timestamp.
    import time as _time
    _date_part = route_name.split('--')[0] + ' ' + route_name.split('--')[1]
    try:
        _route_start = _time.mktime(_time.strptime(_date_part, '%Y-%m-%d %H-%M-%S'))
        # Route name is UTC, but mktime uses local timezone. Force UTC interpretation.
        import calendar as _cal
        _t = _time.strptime(_date_part, '%Y-%m-%d %H-%M-%S')
        _route_start = _cal.timegm(_t)
    except:
        _route_start = 0

    qlogs = sorted(glob.glob(base + '--*/qlog.zst')) + sorted(glob.glob(base + '--*/qlog.bz2'))
    if not qlogs and os.path.isdir(base):
        qlogs = sorted(glob.glob(base + '/qlog.zst')) + sorted(glob.glob(base + '/qlog.bz2'))
    if not qlogs:
        print(json.dumps({'_error': 'No qlog files found for this route.'}))
        sys.exit(0)

    def decompress(path):
        if path.endswith('.zst'):
            dctx = zstandard.ZstdDecompressor()
            with open(path, 'rb') as f:
                return dctx.stream_reader(f).read()
        else:
            with open(path, 'rb') as f:
                return bz2.decompress(f.read())

    data = {'t':[], 'v':[], 'c':[], 'd':[], 'a':[], 'o':[], 'p':[], 'i':[], 'f':[], 'sat':[], 'eng':[], 'trq':[]}
    import bisect
    # Use a list of dicts so all fields for one event are added atomically.
    cs_events = []  # [{t, c, d, a, eng, p, i, f, sat}]
    vs_times = []; vs_v = []; vs_o = []; vs_trq = []
    errors = []
    for path in qlogs:
        try:
            raw = decompress(path)
            events = list(EV.read_multiple_bytes(raw))
            for e in events:
                try: w = e.which()
                except: continue
                if w == 'carState':
                    vs_times.append(e.logMonoTime/1e9)
                    vs_v.append(round(float(e.carState.vEgo), 2))
                    vs_o.append(1 if e.carState.steeringPressed else 0)
                    vs_trq.append(round(float(getattr(e.carState, 'steeringTorque', 0)), 1))
                elif w == 'controlsState':
                    cs = e.controlsState
                    row = {'t': e.logMonoTime/1e9, 'c': 0, 'd': 0, 'a': 0, 'eng': 0, 'p': 0, 'i': 0, 'f': 0, 'sat': 0}
                    try: row['d'] = round(float(cs.desiredCurvature), 5)
                    except: pass
                    try: row['a'] = round(float(cs.curvature), 5)
                    except: pass
                    try: row['eng'] = 1 if cs.enabled else 0
                    except: pass
                    try:
                        pid = cs.lateralControlState.pidState
                        try: row['c'] = round(float(pid.output), 4)
                        except: pass
                        try: row['p'] = round(float(getattr(pid, 'p', 0)), 4)
                        except: pass
                        try: row['i'] = round(float(getattr(pid, 'i', 0)), 4)
                        except: pass
                        try: row['f'] = round(float(getattr(pid, 'f', 0)), 4)
                        except: pass
                        try: row['sat'] = 1 if getattr(pid, 'saturated', False) else 0
                        except: pass
                    except: pass
                    cs_events.append(row)
        except Exception as ex:
            errors.append(os.path.basename(os.path.dirname(path)) + ': ' + str(ex))

    # Convert logMonoTime (seconds since boot) → wall-clock Unix timestamp.
    # Anchor: route name has the real UTC start time. Offset all timestamps from
    # the first event so they show actual wall-clock time.
    first_mono = cs_events[0]['t'] if cs_events else 0
    for row in cs_events:
        # Wall-clock = route_start_utc + elapsed_since_first_event
        t_wall = _route_start + (row['t'] - first_mono)
        t_cs = row['t']  # keep boot time for bisect lookup against vs_times
        data['t'].append(round(t_wall, 2))
        data['c'].append(row['c'])
        data['d'].append(row['d'])
        data['a'].append(row['a'])
        data['eng'].append(row['eng'])
        data['p'].append(row['p'])
        data['i'].append(row['i'])
        data['f'].append(row['f'])
        data['sat'].append(row['sat'])
        if vs_times:
            pos = bisect.bisect_left(vs_times, t_cs)
            if pos == 0:
                nearest = 0
            elif pos >= len(vs_times):
                nearest = len(vs_times) - 1
            else:
                nearest = pos - 1 if (t_cs - vs_times[pos-1]) <= (vs_times[pos] - t_cs) else pos
            data['v'].append(vs_v[nearest])
            data['o'].append(vs_o[nearest])
            data['trq'].append(vs_trq[nearest])
        else:
            data['v'].append(0)
            data['o'].append(0)
            data['trq'].append(0)

    if not data['t'] and errors:
        print(json.dumps({'_error': 'Failed to parse: ' + '; '.join(errors[:3])}))
    elif not data['t']:
        print(json.dumps({'_error': 'No controlsState events found.'}))
    else:
        print(json.dumps(data))
except Exception:
    print(json.dumps({'_error': traceback.format_exc()[-500:]}))
PYEOF
cd /data/openpilot && PYTHONPATH=/data/openpilot /usr/local/venv/bin/python /tmp/kd_parse.py; rm -f /tmp/kd_parse.py
"""
    // Execute — surface the REAL error, don't swallow it with try?
    let output: String
    do {
      output = try await execute(script)
    } catch {
      return (nil, "SSH execute failed: \(error.localizedDescription)")
    }

    // Check if the device returned anything at all
    let trimmed = output.trimmingCharacters(in: .whitespacesAndNewlines)
    if trimmed.isEmpty {
      return (nil, "Device returned empty output. The Python script may have crashed.")
    }

    guard let jsonData = output.data(using: .utf8) else {
      return (nil, "Invalid response encoding")
    }

    // Check for error embedded in JSON
    if let dict = try? JSONSerialization.jsonObject(with: jsonData) as? [String: Any],
       let errMsg = dict["_error"] as? String {
      return (nil, errMsg)
    }

    // Try to decode — if it fails, show the raw output so the user can see what went wrong
    do {
      let parsed = try JSONDecoder().decode(DriveData.self, from: jsonData)
      return (parsed, nil)
    } catch {
      // Show first 500 chars of raw output for debugging
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

  var duration: Double { (t.last ?? 0) - (t.first ?? 0) }
  var durationString: String {
    let m = Int(duration) / 60
    let s = Int(duration) % 60
    return String(format: "%d:%02d", m, s)
  }
  var avgSpeed: Double { v.isEmpty ? 0 : v.reduce(0, +) / Double(v.count) * 3.6 }
  var avgSpeedString: String { String(format: "%.0f km/h", avgSpeed) }
  var maxSpeed: Double { (v.map { $0 * 3.6 }.max()) ?? 0 }
  var maxSpeedString: String { String(format: "%.0f km/h", maxSpeed) }

  var avgStep: Double {
    guard c.count > 1 else { return 0 }
    var total = 0.0
    for i in 1..<c.count { total += abs(c[i] - c[i-1]) }
    return total / Double(c.count - 1)
  }
  var maxStep: Double {
    guard c.count > 1 else { return 0 }
    var mx = 0.0
    for i in 1..<c.count { mx = Swift.max(mx, abs(c[i] - c[i-1])) }
    return mx
  }
  var overrides: Int { o.filter { $0 == 1 }.count }

  /// Plain-English guidance on what the data suggests for tuning.
  var tuningGuidance: String {
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
    return lines.joined(separator: "\n")
  }
}
