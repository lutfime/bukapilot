import SwiftUI

@main
struct KommuDriveApp: App {
  @StateObject private var session = DriveSessionViewModel()

  var body: some Scene {
    WindowGroup {
      RootView(viewModel: session)
    }
  }
}

/// Routes between the connection screen and the drive screen based on BLE state.
struct RootView: View {
  @ObservedObject var viewModel: DriveSessionViewModel

  var body: some View {
    Group {
      if viewModel.connectionState.isConnected {
        DriveView(viewModel: viewModel)
      } else {
        ConnectionView(viewModel: viewModel)
      }
    }
    .preferredColorScheme(.dark)
  }
}
