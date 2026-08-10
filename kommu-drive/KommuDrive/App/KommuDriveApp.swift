import SwiftUI

@main
struct KommuDriveApp: App {
  @StateObject private var session = DriveSessionViewModel()
  @Environment(\.scenePhase) private var scenePhase

  var body: some Scene {
    WindowGroup {
      RootView(viewModel: session)
        .onAppear {
          HotspotDetector.shared.requestPermission()
          HotspotDetector.refreshSSID()
        }
        .onChange(of: scenePhase) { _, phase in
          switch phase {
          case .active:
            HotspotDetector.refreshSSID()
            // App came back to foreground — try to restore the BLE connection.
            session.handleAppBecameActive()
          case .background, .inactive:
            break
          @unknown default:
            break
          }
        }
    }
  }
}

/// Root container with the persistent bottom tab bar.
/// The tab bar is ALWAYS visible — even before BLE connects — so SSH features
/// (Tuning, Drives, Settings) are reachable without waiting for Bluetooth.
struct RootView: View {
  @ObservedObject var viewModel: DriveSessionViewModel
  @State private var selectedTab: DriveView.Tab = .drive

  var body: some View {
    VStack(spacing: 0) {
      // Content area — switches by tab
      ZStack {
        switch selectedTab {
        case .drive:
          driveTabContent
        case .tuning:
          TuningSheet(viewModel: viewModel)
        case .drives:
          DriveBrowserSheet(viewModel: viewModel)
        case .logs:
          LogsView(viewModel: viewModel)
        case .settings:
          SettingsSheet(viewModel: viewModel)
        }
      }
      .frame(maxWidth: .infinity, maxHeight: .infinity)

      // Persistent tab bar
      DriveView.tabBar(selectedTab: $selectedTab)
    }
    .preferredColorScheme(.dark)
  }

  /// Drive tab content: shows ConnectionView if not BLE-connected, or the live
  /// drive HUD if connected. During reconnect, shows HUD + banner.
  @ViewBuilder
  private var driveTabContent: some View {
    if viewModel.connectionState.isConnected {
      DriveView(viewModel: viewModel)
    } else if viewModel.autoReconnectEnabled,
              case .connecting = viewModel.connectionState,
              viewModel.latestFrame != nil,
              !viewModel.reconnectTimedOut {
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
