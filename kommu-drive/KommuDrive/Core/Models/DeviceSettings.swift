import Foundation

/// One Wi-Fi network from a device scan result.
struct WifiNetwork: Identifiable, Equatable {
  let ssid: String
  let needsPassword: Bool
  var id: String { ssid }
}

/// Subset of the `CHANNEL_SETTINGS` msgpack payload.
/// Mirrors `appbridged.py:send_settings_message`. Only the fields Phase 1/2 needs
/// are decoded; the rest are dropped harmlessly.
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
  let hotspotSsid: String?       // Kommu hotspot SSID (sent when hotspot enabled) — shown so the user can join
  let hotspotPassword: String?   // Kommu hotspot password (sent when hotspot enabled)

  // Software toggles (these are the ones the kommu app shows + can save via saveToggle)
  let enabled: Bool              // OpenpilotEnabledToggle
  let quietMode: Bool            // QuietMode
  let experimentalMode: Bool     // ConditionalExperimentalMode
  let alcEnabled: Bool           // IsAlcEnabled (auto-lane-centering?)
  let ldwEnabled: Bool           // IsLdwEnabled (lane departure warning)
  let recordFront: Bool          // RecordFront (dashcam record driver-facing cam)
  let sshEnabled: Bool           // SshEnabled
  let usePidController: Bool     // X70UsePidController (X70 only: PID vs torque lateral controller)
  let useSupercomboModel: Bool   // UseSupercomboModel (0.11 fused model vs 0.10 split)

  // Update
  let updateAvailable: Bool
  let updaterFetchAvailable: Bool
  let updaterTargetBranch: String?
  let updaterState: String?

  // Car
  let carName: String?
  let drivePathOffset: String?
  let brakeMagGain: String?

  /// Wi-Fi scan results (transient — only present in the frame after a scan).
  let wifiList: [WifiNetwork]

  /// Comma-separated list of branches the updater can switch to.
  let availableBranches: String?

  static let empty = DeviceSettings(
    dongleID: nil, gitCommit: nil, currentVersion: nil, osVersion: nil,
    state: nil, isMetric: false, isOffroad: true, localIP: nil,
    activeWlanSSID: nil, networkType: nil, simStatus: nil, hotspotSsid: nil, hotspotPassword: nil,
    enabled: false, quietMode: false, experimentalMode: false,
    alcEnabled: false, ldwEnabled: false, recordFront: false, sshEnabled: false,
    usePidController: false, useSupercomboModel: false,
    updateAvailable: false, updaterFetchAvailable: false,
    updaterTargetBranch: nil, updaterState: nil,
    carName: nil, drivePathOffset: nil, brakeMagGain: nil,
    wifiList: [], availableBranches: nil
  )

  static func decode(_ raw: [String: Any]) -> DeviceSettings {
    func str(_ k: String) -> String? { raw[k] as? String }
    func bool(_ k: String) -> Bool { (raw[k] as? Bool) ?? ((raw[k] as? Int).map { $0 != 0 }) ?? false }

    let wifiList: [WifiNetwork] = {
      guard let arr = raw["wifiList"] as? [Any] else { return [] }
      return arr.compactMap { item -> WifiNetwork? in
        guard let d = item as? [String: Any], let ssid = d["ssid"] as? String else { return nil }
        let pw = (d["password"] as? Bool) ?? false
        return WifiNetwork(ssid: ssid, needsPassword: pw)
      }
    }()

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
      hotspotSsid: str("hotspotSsid"),
      hotspotPassword: str("hotspotPassword"),
      enabled: bool("OpenpilotEnabledToggle"),
      quietMode: bool("QuietMode"),
      experimentalMode: bool("ConditionalExperimentalMode"),
      alcEnabled: bool("IsAlcEnabled"),
      ldwEnabled: bool("IsLdwEnabled"),
      recordFront: bool("RecordFront"),
      sshEnabled: bool("SshEnabled"),
      usePidController: bool("X70UsePidController"),
      useSupercomboModel: bool("UseSupercomboModel"),
      updateAvailable: bool("UpdateAvailable"),
      updaterFetchAvailable: bool("UpdaterFetchAvailable"),
      updaterTargetBranch: str("UpdaterTargetBranch"),
      updaterState: str("UpdaterState"),
      carName: str("CarName"),
      drivePathOffset: str("DrivePathOffset"),
      brakeMagGain: str("BrakeMagGain"),
      wifiList: wifiList,
      availableBranches: str("UpdaterAvailableBranches")
    )
  }
}
