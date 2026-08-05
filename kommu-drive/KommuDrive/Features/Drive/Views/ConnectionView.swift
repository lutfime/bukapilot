import SwiftUI

/// Scan + connect screen. Shown until a peripheral is connected.
///
/// Mirrors the connection UX needed for Phase 1: scan for KA2 devices
/// advertising the Nordic UART service, list them by RSSI, and tap to connect.
struct ConnectionView: View {

  @ObservedObject var viewModel: DriveSessionViewModel

  var body: some View {
    VStack(spacing: 0) {
      header
      Divider().background(Color.white.opacity(0.1))

      if case .failed(let msg) = viewModel.connectionState {
        errorBanner(msg: msg)
      }

      List {
        Section {
          if viewModel.discoveredPeripherals.isEmpty {
            emptyState
          } else {
            ForEach(viewModel.discoveredPeripherals) { peripheral in
              Button {
                viewModel.connect(to: peripheral.id)
              } label: {
                peripheralRow(peripheral)
              }
              .buttonStyle(.plain)
            }
          }
        } header: {
          Text("Discovered devices")
            .font(.system(size: 12, weight: .semibold))
            .foregroundStyle(.secondary)
            .textCase(nil)
        }

        // Fallback: show every nearby device if the KA2 wasn't auto-matched.
        if viewModel.showAllDevices && !viewModel.otherPeripherals.isEmpty {
          Section {
            ForEach(viewModel.otherPeripherals) { peripheral in
              Button {
                viewModel.connect(to: peripheral.id)
              } label: {
                peripheralRow(peripheral)
              }
              .buttonStyle(.plain)
            }
          } header: {
            Text("All nearby devices")
              .font(.system(size: 12, weight: .semibold))
              .foregroundStyle(.secondary)
            .textCase(nil)
          }
        }
      }
      .listStyle(.plain)
      .scrollContentBackground(.hidden)
      .background(Color.clear)

      Spacer()
      footer
    }
    .background(
      LinearGradient(
        colors: [Color(red: 0.06, green: 0.07, blue: 0.10),
                 Color(red: 0.10, green: 0.11, blue: 0.14)],
        startPoint: .top, endPoint: .bottom
      )
      .ignoresSafeArea()
    )
    .onAppear {
      viewModel.startScanning()
    }
    .onDisappear {
      viewModel.stopScanning()
    }
  }

  // MARK: Pieces

  private var header: some View {
    HStack {
      VStack(alignment: .leading, spacing: 2) {
        Text("KommuDrive")
          .font(.system(size: 28, weight: .bold, design: .rounded))
          .foregroundStyle(.white)
        Text(scanStatus)
          .font(.system(size: 13, design: .monospaced))
          .foregroundStyle(.secondary)
      }
      Spacer()
      ProgressView()
        .tint(Color.accentColor)
        .foregroundStyle(Color.accentColor)
        .opacity(isScanning ? 1 : 0)
    }
    .padding()
  }

  private var scanStatus: String {
    switch viewModel.connectionState {
    case .scanning: return "Scanning for KA2…"
    case .connecting: return "Connecting…"
    case .connected(let name): return "Connected: \(name)"
    case .failed(let m): return "Failed: \(m)"
    case .disconnected: return "Tap scan to look for devices"
    }
  }

  private var isScanning: Bool {
    if case .scanning = viewModel.connectionState { return true }
    return false
  }

  private var emptyState: some View {
    VStack(spacing: 12) {
      Image(systemName: "antenna.radiowaves.left.and.right")
        .font(.system(size: 36))
        .foregroundStyle(.tertiary)
      Text("No devices found yet")
        .font(.system(size: 14, weight: .medium))
        .foregroundStyle(.secondary)
      Text("Make sure your KA2 is powered on and nearby, and that the kommu app isn't already connected to it (BLE allows only one connection at a time).")
        .font(.system(size: 12))
        .foregroundStyle(.tertiary)
        .multilineTextAlignment(.center)
        .padding(.horizontal, 24)

      // Show-all fallback: if auto-match misses your device, surface everything
      // nearby so you can pick it manually.
      Button {
        viewModel.showAllDevices = true
      } label: {
        Label("Show all nearby devices", systemImage: "list.bullet")
          .font(.system(size: 13, weight: .semibold))
      }
      .buttonStyle(.bordered)
      .tint(.white)
      .opacity(viewModel.showAllDevices ? 0 : 1)
    }
    .frame(maxWidth: .infinity)
    .padding(.vertical, 32)
    .listRowBackground(Color.clear)
  }

  private func peripheralRow(_ p: BLEManager.DiscoveredPeripheral) -> some View {
    HStack(spacing: 12) {
      Image(systemName: "car.fill")
        .font(.system(size: 18))
        .foregroundStyle(Color.accentColor)
        .frame(width: 32, height: 32)
        .background(Color.accentColor.opacity(0.12), in: RoundedRectangle(cornerRadius: 8))
      VStack(alignment: .leading, spacing: 2) {
        Text(p.name)
          .font(.system(size: 15, weight: .semibold))
          .foregroundStyle(.white)
        Text(rssiLabel(p.rssi))
          .font(.system(size: 11, design: .monospaced))
          .foregroundStyle(.secondary)
      }
      Spacer()
      Image(systemName: "chevron.right")
        .font(.system(size: 12, weight: .semibold))
        .foregroundStyle(.tertiary)
    }
    .padding(.vertical, 6)
    .listRowBackground(Color.white.opacity(0.04))
  }

  private func rssiLabel(_ rssi: Int) -> String {
    let bars: Int
    switch rssi {
    case 35...:    bars = 4
    case 25..<35:  bars = 3
    case 15..<25:  bars = 2
    default:       bars = 1
    }
    // RSSI is negative; show magnitude bars + dBm.
    let bar = String(repeating: "▰", count: bars) + String(repeating: "▱", count: 4 - bars)
    return "\(bar)  -\(abs(rssi)) dBm"
  }

  private func errorBanner(msg: String) -> some View {
    HStack(spacing: 8) {
      Image(systemName: "exclamationmark.triangle.fill")
        .foregroundStyle(.orange)
      Text(msg)
        .font(.system(size: 12))
        .foregroundStyle(.secondary)
      Spacer()
    }
    .padding(10)
    .background(Color.orange.opacity(0.12))
  }

  private var footer: some View {
    HStack(spacing: 14) {
      Button {
        viewModel.startScanning()
      } label: {
        Label("Rescan", systemImage: "arrow.clockwise")
          .font(.system(size: 14, weight: .semibold))
      }
      .buttonStyle(.bordered)
      .tint(.white)

      #if DEBUG
      Button {
        viewModel.latestFrame = DriveViewPreview.sampleFrame
        viewModel._debugInjectIndicators(confidence: 0.82, steering: 0.6)
        viewModel.framesReceived = 256
      } label: {
        Label("Demo Drive", systemImage: "play.fill")
          .font(.system(size: 13, weight: .semibold))
      }
      .buttonStyle(.borderedProminent)
      .tint(Color.accentColor)
      #endif

      Spacer()

      Text("KommuDrive · Phase 1")
        .font(.system(size: 11, design: .monospaced))
        .foregroundStyle(.tertiary)
    }
    .padding()
  }
}
