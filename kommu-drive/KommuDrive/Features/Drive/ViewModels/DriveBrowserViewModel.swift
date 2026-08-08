import Foundation
import SwiftUI

/// ViewModel for the drive browser: manages route list + drive data + loading states.
/// Uses DeviceService (SSH) to fetch data from the device.
@MainActor
final class DriveBrowserViewModel: ObservableObject {
  @Published var routes: [String] = []
  @Published var selectedRoute: String? = nil
  @Published var driveData: DriveData? = nil
  @Published var isLoading = false
  @Published var isConnected = false
  @Published var error: String? = nil

  private let service = DeviceService()

  func connect(host: String) async {
    isLoading = true; error = nil
    isConnected = await service.connect(host: host)
    if isConnected {
      await loadRoutes()
    } else {
      error = "SSH connection failed. Check device IP (\(host))."
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
    let data = await service.fetchDriveData(route: route)
    if let data = data, !data.isEmpty {
      driveData = data
    } else {
      error = "Failed to load drive data for \(route)."
    }
    isLoading = false
  }

  func disconnect() {
    service.disconnect()
    isConnected = false
  }
}
