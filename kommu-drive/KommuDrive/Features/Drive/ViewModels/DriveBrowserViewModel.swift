import Foundation
import SwiftUI

/// ViewModel for the drive browser: manages route list + drive data + loading states.
/// Uses DeviceService (SSH) to fetch data from the device.
@MainActor
final class DriveBrowserViewModel: ObservableObject {
  @Published var routes: [DeviceService.Drive] = []
  @Published var selectedRoute: String? = nil
  @Published var driveData: DriveData? = nil
  @Published var isLoading = false
  @Published var isConnected = false
  @Published var error: String? = nil

  private let service = DeviceService()

  func connect(host: String) async {
    isLoading = true; error = nil
    do {
      try await service.connect(host: host)
      isConnected = true
      await loadRoutes()
    } catch let err {
      isConnected = false
      error = service.connectionError ?? "SSH connection failed: \(err.localizedDescription)"
    }
    isLoading = false
  }

  func loadRoutes() async {
    let r = await service.fetchRoutes()
    routes = r
    if r.isEmpty { error = "No drives found on device." }
  }

  func selectRoute(_ route: String) async {
    selectedRoute = route
    driveData = nil; isLoading = true; error = nil
    // Reconnect if the SSH session dropped (e.g. idle timeout between list and fetch)
    if !service.isConnected {
      guard let host = DeviceService.cachedIP else {
        error = "SSH disconnected and no cached IP to reconnect."
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
    let result = await service.fetchDriveData(route: route)
    if let data = result.data, !data.isEmpty {
      driveData = data
    } else {
      error = result.error ?? "No data found for this drive."
    }
    isLoading = false
  }

  func disconnect() {
    service.disconnect()
    isConnected = false
  }
}
