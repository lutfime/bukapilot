import Foundation

/// Subset of the `CHANNEL_SETTINGS` msgpack payload.
/// Mirrors `appbridged.py:send_settings_message`. Only the fields Phase 1 needs are
/// decoded; the rest are dropped harmlessly.
struct DeviceSettings: Equatable {
  let dongleID: String?
  let gitCommit: String?
  let currentVersion: String?
  let osVersion: String?
  let state: String?
  let isMetric: Bool
  let isOffroad: Bool
  let localIP: String?
  let activeWlanSSID: String?
  let networkType: String?
  let simStatus: String?
  let enabled: Bool           // OpenpilotEnabledToggle
  let sshEnabled: Bool
  let updateAvailable: Bool

  static let empty = DeviceSettings(
    dongleID: nil, gitCommit: nil, currentVersion: nil, osVersion: nil,
    state: nil, isMetric: false, isOffroad: true, localIP: nil,
    activeWlanSSID: nil, networkType: nil, simStatus: nil,
    enabled: false, sshEnabled: false, updateAvailable: false
  )

  static func decode(_ raw: [String: Any]) -> DeviceSettings {
    func str(_ k: String) -> String? { raw[k] as? String }
    func bool(_ k: String) -> Bool { (raw[k] as? Bool) ?? ((raw[k] as? Int).map { $0 != 0 }) ?? false }
    return DeviceSettings(
      dongleID: str("dongleID"),
      gitCommit: str("gitCommit"),
      currentVersion: str("currentVersion"),
      osVersion: str("osVersion"),
      state: str("state"),
      isMetric: bool("IsMetric"),
      isOffroad: bool("isOffroad"),
      localIP: str("localIP"),
      activeWlanSSID: str("activeWlanSSID"),
      networkType: str("networkType"),
      simStatus: str("simStatus"),
      enabled: bool("OpenpilotEnabledToggle"),
      sshEnabled: bool("SshEnabled"),
      updateAvailable: bool("UpdateAvailable")
    )
  }
}
