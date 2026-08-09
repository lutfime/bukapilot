import Foundation
import NetworkExtension
import CoreLocation

/// Detects whether the iPhone is connected to the device's hotspot.
/// Uses NEHotspotNetwork.fetchCurrent (iOS 14+) since CNCopyCurrentNetworkInfo
/// is deprecated and returns nil on iOS 26 SDK.
final class HotspotDetector: NSObject, CLLocationManagerDelegate {
  static let shared = HotspotDetector()
  private let locationManager = CLLocationManager()
  private var requestedPermission = false

  // Cached SSID (fetchCurrent is async — we cache the result)
  private static var cachedSSID: String?
  private static var lastFetch: Date?

  override init() {
    super.init()
    locationManager.delegate = self
  }

  /// Requests location permission (needed for SSID access).
  func requestPermission() {
    guard !requestedPermission else { return }
    requestedPermission = true
    locationManager.requestWhenInUseAuthorization()
  }

  /// Refreshes the cached SSID via NEHotspotNetwork.fetchCurrent.
  /// Call this on app launch / foreground. It's async but we cache the result.
  static func refreshSSID() {
    NEHotspotNetwork.fetchCurrent { network in
      if let ssid = network?.ssid {
        cachedSSID = ssid
        lastFetch = Date()
        AppLog.info("WiFi SSID: \(ssid)")
      } else {
        AppLog.warn("fetchCurrent returned no SSID")
      }
    }
  }

  /// The cached SSID (refreshed periodically).
  static var currentSSID: String? {
    // Refresh if stale (more than 30s old)
    if let last = lastFetch, Date().timeIntervalSince(last) > 30 {
      refreshSSID()
    }
    // If never fetched, fetch now
    if cachedSSID == nil && lastFetch == nil {
      refreshSSID()
    }
    return cachedSSID
  }

  /// True if the iPhone is connected to the device's hotspot (SSID contains "kommu").
  static var isOnDeviceHotspot: Bool {
    guard let ssid = currentSSID else { return false }
    return ssid.lowercased().contains("kommu")
  }
}
