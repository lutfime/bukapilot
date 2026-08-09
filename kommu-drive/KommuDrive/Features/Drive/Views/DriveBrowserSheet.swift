import SwiftUI

/// Drive browser: lists drives on the device → tap one → see charts.
/// Uses DriveBrowserViewModel (which uses DeviceService via SSH).
struct DriveBrowserSheet: View {
  @ObservedObject var viewModel: DriveSessionViewModel
  @StateObject private var browserVM = DriveBrowserViewModel()
  @State private var segment: DriveSegment = .drives

  private enum DriveSegment: String, CaseIterable, Identifiable {
    case drives = "Drives"
    case mapCorner = "Map Corner"
    var id: String { rawValue }
  }

  private var hostLabel: String {
    DeviceService.hostLabel(ssid: viewModel.settings.activeWlanSSID)
  }
  private var hostDetail: String {
    DeviceService.resolveHost(hotspotIp: viewModel.settings.hotspotIp, localIP: viewModel.settings.localIP) ?? "no IP"
  }

  var body: some View {
    NavigationStack {
      VStack(spacing: 0) {
        // Segmented control — hidden when drilling into a specific drive's analysis.
        if browserVM.selectedRoute == nil {
          Picker("", selection: $segment) {
            ForEach(DriveSegment.allCases) { seg in
              Text(seg.rawValue).tag(seg)
            }
          }
          .pickerStyle(.segmented)
          .padding(.horizontal)
          .padding(.vertical, 8)
        }

        switch segment {
        case .drives:
          drivesContent
        case .mapCorner:
          MapdStatusView(viewModel: viewModel)
        }
      }
      .navigationTitle(navTitle)
      .navigationBarTitleDisplayMode(.inline)
      .toolbar {
        if browserVM.selectedRoute != nil {
          ToolbarItem(placement: .topBarLeading) {
            Button {
              browserVM.selectedRoute = nil
              browserVM.error = nil
            } label: {
              Label("Drives", systemImage: "chevron.left")
            }
          }
        }
      }
      .onAppear { connectWithRetry() }
    }
  }

  private var navTitle: String {
    if browserVM.selectedRoute != nil { return "Analysis" }
    return segment == .drives ? "Drives" : "Map Corner"
  }

  @ViewBuilder
  private var drivesContent: some View {
    if !browserVM.isConnected && browserVM.routes.isEmpty {
      connectingView
    } else if browserVM.selectedRoute != nil {
      if browserVM.isLoading {
        VStack(spacing: 16) {
          ProgressView(value: browserVM.progress)
            .progressViewStyle(.linear)
            .frame(maxWidth: 240)
          Text(browserVM.progressText.isEmpty ? "Loading…" : browserVM.progressText)
            .font(.system(size: 13))
            .foregroundStyle(.secondary)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
      } else if let chartVM = browserVM.chartsVM {
        DriveChartsView(vm: chartVM)
      } else if let err = browserVM.error {
        VStack(spacing: 12) {
          Image(systemName: "exclamationmark.triangle.fill").foregroundStyle(.orange)
          Text(err)
            .font(.system(size: 12, design: .monospaced))
            .foregroundStyle(.secondary)
            .multilineTextAlignment(.center)
            .textSelection(.enabled)
          Button("Copy error") {
            UIPasteboard.general.string = err
          }
          .buttonStyle(.bordered)
          .tint(.secondary)
          Button("Back to drives") {
            browserVM.selectedRoute = nil
            browserVM.error = nil
          }
          .buttonStyle(.bordered)
        }
        .padding()
      }
    } else {
      routeListView
    }
  }

  /// Connects using cached IP (instant) or live BLE IP.
  private func connectWithRetry() {
    Task {
      let host = DeviceService.resolveHost(hotspotIp: viewModel.settings.hotspotIp, localIP: viewModel.settings.localIP)
      guard let host = host, !host.isEmpty else {
        await MainActor.run {
          browserVM.error = "No device IP yet. Connect to the device via BLE once, then it's cached for SSH."
        }
        return
      }
      await browserVM.connect(host: host)
    }
  }

  private var connectingView: some View {
    VStack(spacing: 16) {
      if browserVM.isLoading {
        VStack(spacing: 6) {
          ProgressView()
          Text("Connecting to \(hostLabel)…")
            .font(.system(size: 13))
          Text("SSH to \(hostDetail)")
            .font(.system(size: 11, design: .monospaced))
            .foregroundStyle(.tertiary)
        }
      } else if let err = browserVM.error {
        Image(systemName: "exclamationmark.triangle.fill").foregroundStyle(.orange)
        Text(err)
          .font(.system(size: 12, design: .monospaced))
          .foregroundStyle(.secondary)
          .multilineTextAlignment(.center)
          .textSelection(.enabled)
          .padding(.horizontal, 24)
        Button("Copy error") {
          UIPasteboard.general.string = err
        }
        .buttonStyle(.bordered)
        .tint(.secondary)
        Button("Retry") {
          browserVM.error = nil
          connectWithRetry()
        }
        .buttonStyle(.bordered)
      }
    }
    .frame(maxWidth: .infinity, maxHeight: .infinity)
  }

  private var routeListView: some View {
    List {
      if let err = browserVM.error, !browserVM.isConnected {
        Text(err)
          .font(.system(size: 12))
          .foregroundStyle(.orange)
          .listRowBackground(Color.orange.opacity(0.08))
      }
      if browserVM.routes.isEmpty && !browserVM.isLoading {
        Text("No drives found").foregroundStyle(.secondary)
      }
      ForEach(browserVM.routes) { drive in
        Button {
          Task { await browserVM.selectRoute(drive.route) }
        } label: {
          let rvm = drive.routeVM
          HStack {
            Image(systemName: "car.fill").foregroundStyle(.secondary)
            VStack(alignment: .leading) {
              Text(rvm.dateLabel).font(.system(size: 14, weight: .medium))
              HStack(spacing: 6) {
                Text(rvm.rawValue).font(.system(size: 10, design: .monospaced)).foregroundStyle(.tertiary)
                if rvm.hasMultipleSegments {
                  Text("\(rvm.segmentCount) segs").font(.system(size: 10, weight: .semibold))
                    .foregroundStyle(.secondary)
                    .padding(.horizontal, 6).padding(.vertical, 1)
                    .background(Color.secondary.opacity(0.12), in: Capsule())
                }
              }
            }
            Spacer()
            Image(systemName: "chevron.right").font(.system(size: 12)).foregroundStyle(.tertiary)
          }
        }
        .buttonStyle(.plain)
      }
    }
  }
}
