import SwiftUI

@main
struct KommuDriveApp: App {
  @StateObject private var session = DriveSessionViewModel()
  @Environment(\.scenePhase) private var scenePhase

  var body: some Scene {
    WindowGroup {
      RootView(viewModel: session)
        .onChange(of: scenePhase) { _, phase in
          switch phase {
          case .active:
            // App came back to foreground — try to restore the BLE connection.
            // iOS drops BLE links shortly after backgrounding, so we need to
            // reconnect or rescan every time the user returns.
            session.handleAppBecameActive()
          case .background, .inactive:
            // App backgrounded — nothing to do; iOS will suspend us and drop
            // the BLE link. The central delegate will fire didDisconnect.
            break
          @unknown default:
            break
          }
        }
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
      if viewModel.connectionState.isConnected {
        DriveView(viewModel: viewModel)
      } else if viewModel.autoReconnectEnabled,
                case .connecting = viewModel.connectionState,
                viewModel.latestFrame != nil,
                !viewModel.reconnectTimedOut {
        // Reconnecting after a drop — keep the drive view with a banner, but
        // only if we had real data before and the reconnect hasn't timed out.
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
