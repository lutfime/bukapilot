import Foundation
import SwiftUI

/// ViewModel for the drive browser: manages route list + drive data + loading states.
/// Uses DeviceService (SSH) to fetch data from the device.

final class DriveBrowserViewModel: ObservableObject {
  @Published var routes: [DeviceService.Drive] = []
  @Published var selectedRoute: String? = nil
  @Published var chartsVM: DriveChartsViewModel? = nil
  @Published var isLoading = false
  @Published var isConnected = false
  @Published var error: String? = nil
  @Published var progress: Double = 0
  @Published var progressText: String = ""

  private let service = DeviceService()

  func connect(host: String) async {
    isLoading = true; error = nil

    // Always show cached drives first (instant, offline)
    let cached = DeviceService.cachedDrives()
    if !cached.isEmpty {
      routes = cached
    }

    // Try to connect for live route list
    do {
      try await service.connect(host: host)
      isConnected = true
      await loadRoutes()
    } catch let err {
      isConnected = false
      // Don't overwrite routes if we have cached ones
      if cached.isEmpty {
        error = service.connectionError ?? "SSH connection failed: \(err.localizedDescription)"
      } else {
        error = "⚠️ Device offline. Showing \(cached.count) cached drive\(cached.count == 1 ? "" : "s")."
      }
    }
    isLoading = false
  }

  func loadRoutes() async {
    let r = await service.fetchRoutes()
    routes = r
    if r.isEmpty { error = "No drives found on device." }
  }

  @MainActor
  func selectRoute(_ route: String) async {
    selectedRoute = route
    chartsVM = nil; isLoading = true; error = nil
    progress = 0; progressText = "Starting…"

    // Try cached data first (works offline, instant)
    if let cached = service.loadCachedDriveData(route: route), !cached.isEmpty {
      AppLog.info("selectRoute: cache hit for \(route), \(cached.t.count) points")
      progressText = "Preparing charts…"

      let driveRoute = DriveRouteVM(rawRoute: route)
      let chartVM = DriveChartsViewModel(route: driveRoute)

      await chartVM.load(data: cached)
      self.chartsVM = chartVM
      isLoading = false
      AppLog.info("selectRoute: chartsVM loaded")
      return
    }

    AppLog.info("selectRoute: cache miss for \(route), fetching via SSH")

    // Not cached — need SSH
    if !service.isConnected {
      guard let host = DeviceService.resolveHost(hotspotIp: nil, localIP: DeviceService.cachedIP) else {
        error = "Not cached and no device IP to fetch."
        isLoading = false
        return
      }
      do {
        try await service.connect(host: host)
      } catch let err {
        error = service.connectionError ?? "Reconnect failed: \(err.localizedDescription)"
        isLoading = false
        return
      }
    }

    let result = await service.fetchDriveData(route: route) { pct, text in
      Task { @MainActor in
        self.progress = pct
        self.progressText = text
      }
    }

    if let data = result.data, !data.isEmpty {
      AppLog.info("selectRoute: fetched \(data.t.count) points, loading chartsVM")
      let driveRoute = DriveRouteVM(rawRoute: route)
      let chartVM = DriveChartsViewModel(route: driveRoute)
      await chartVM.load(data: data)
      self.chartsVM = chartVM
      isLoading = false
      AppLog.info("selectRoute: chartsVM loaded")
    } else {
      self.error = result.error ?? "No data found for this drive."
      isLoading = false
    }
  }

  func disconnect() {
    service.disconnect()
    isConnected = false
  }
}
