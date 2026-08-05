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
/// During a reconnect attempt we keep the DriveView visible with an overlay so
/// the user isn't bounced back to the scan screen on every transient drop.
struct RootView: View {
  @ObservedObject var viewModel: DriveSessionViewModel

  var body: some View {
    Group {
      if viewModel.connectionState.isConnected || viewModel.latestFrame != nil {
        DriveView(viewModel: viewModel)
      } else if viewModel.ble.autoReconnectEnabled, case .connecting = viewModel.connectionState {
        // Reconnecting — keep the drive view but show a banner.
        ZStack {
          DriveView(viewModel: viewModel)
          VStack {
            reconnectBanner
            Spacer()
          }
        }
      } else {
        ConnectionView(viewModel: viewModel)
      }
    }
    .preferredColorScheme(.dark)
  }

  private var reconnectBanner: some View {
    HStack(spacing: 10) {
      ProgressView().tint(.white)
      Text("Reconnecting to device…")
        .font(.system(size: 14, weight: .semibold))
        .foregroundStyle(.white)
    }
    .padding(.horizontal, 16).padding(.vertical, 10)
    .background(.ultraThinMaterial, in: Capsule())
    .shadow(radius: 8)
    .padding(.top, 8)
  }
}
