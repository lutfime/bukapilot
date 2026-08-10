import SwiftUI

/// Settings sheet shown from the DriveView gear button.
/// Mirrors the sections the kommu app shows: device info, software toggles,
/// software update, Wi-Fi, and device actions (reboot, reset calibration).
struct SettingsSheet: View {
  @ObservedObject var viewModel: DriveSessionViewModel
  @Environment(\.dismiss) private var dismiss

  // Live toggle states — synced from device each frame.
  @State private var experimentalMode: Bool = false
  @State private var assistedLaneChange: Bool = false
  @State private var quietMode: Bool = false
  @State private var laneDepartureWarning: Bool = false
  @State private var recordDriverCamera: Bool = false
  @State private var sshEnabled: Bool = false
  @State private var usePidController: Bool = false
  @State private var useSupercomboModel: Bool = false
  @State private var madsEnabled: Bool = false
  // Once the user taps a PID/model/MADS toggle, stop echo-syncing it so the displayed
  // value doesn't get overwritten by a lagging echo and visually "dance" on/off.
  @State private var pidTouched: Bool = false
  @State private var modelTouched: Bool = false
  @State private var madsTouched: Bool = false

  @State private var showRebootConfirm = false
  @State private var showClearCacheConfirm = false
  @State private var wifiPasswordEntry: WifiNetwork? = nil
  @State private var wifiPasswordInput = ""
  @State private var showForgetWifiConfirm = false

  var body: some View {
    NavigationStack {
      List {
        deviceSection
        softwareSettingsSection
        updateSection
        wifiSection
        deviceActionsSection
        cacheSection
        connectionSection
      }
      .navigationTitle("Settings")
      .navigationBarTitleDisplayMode(.inline)
      .onAppear {
        // Switch the device to streaming settings so the panel populates.
        viewModel.requestSettings()
        syncToggles()
      }
      .onDisappear {
        // Switch back to visualisation when leaving the panel.
        viewModel.requestVisualisation()
      }
      .onReceive(viewModel.$settings) { _ in syncToggles() }
      .alert("Reboot device?", isPresented: $showRebootConfirm) {
        Button("Reboot", role: .destructive) {
          viewModel.rebootDevice()
          dismiss()
        }
        Button("Cancel", role: .cancel) {}
      } message: {
        Text("The device will restart. This only works when openpilot is disabled.")
      }
      .alert("Forget Wi-Fi?", isPresented: $showForgetWifiConfirm) {
        Button("Forget", role: .destructive) {
          if let ssid = viewModel.settings.activeWlanSSID {
            viewModel.forgetWifi(ssid: ssid)
          }
        }
        Button("Cancel", role: .cancel) {}
      } message: {
        if let ssid = viewModel.settings.activeWlanSSID {
          Text("Remove saved network '\(ssid)' from the device?")
        }
      }
      .alert("Clear Drive Cache?", isPresented: $showClearCacheConfirm) {
        Button("Clear", role: .destructive) {
          DeviceService.clearCache()
          cacheSizeDisplay = "0 B"
        }
        Button("Cancel", role: .cancel) {}
      } message: {
        Text("Delete all \(DeviceService.cacheCount) cached drives (\(DeviceService.cacheSizeString))? They'll need to be re-parsed from the device next time.")
      }
      .sheet(item: $wifiPasswordEntry) { network in
        WifiPasswordSheet(
          ssid: network.ssid,
          onSubmit: { password in
            viewModel.connectWifi(ssid: network.ssid, password: password)
            wifiPasswordEntry = nil
          },
          onCancel: { wifiPasswordEntry = nil }
        )
      }
    }
  }

  // MARK: Sections

  private var deviceSection: some View {
    Section {
      infoRow("Dongle ID", viewModel.settings.dongleID ?? "—")
      infoRow("Version", viewModel.settings.currentVersion ?? "—")
      infoRow("Git Commit", viewModel.settings.gitCommit ?? "—")
      infoRow("OS", viewModel.settings.osVersion ?? "—")
      infoRow("Car", viewModel.settings.carName ?? "—")
      infoRow("Network", viewModel.settings.networkType ?? "—")
      infoRow("IP", viewModel.settings.localIP ?? "—")
      infoRow("SIM", viewModel.settings.simStatus ?? "—")
    } header: {
      Text("Device")
    }
  }

  private var softwareSettingsSection: some View {
    Section {
      toggleRow("Experimental Mode", isOn: $experimentalMode, key: "ConditionalExperimentalMode", deviceValue: viewModel.settings.experimentalMode)
      toggleRow("Assisted Lane Change", isOn: $assistedLaneChange, key: "IsAlcEnabled", deviceValue: viewModel.settings.alcEnabled)
      toggleRow("Lane Departure Warning", isOn: $laneDepartureWarning, key: "IsLdwEnabled", deviceValue: viewModel.settings.ldwEnabled)
      toggleRow("Quiet Mode", isOn: $quietMode, key: "QuietMode", deviceValue: viewModel.settings.quietMode)
      toggleRow("Record Driver Camera", isOn: $recordDriverCamera, key: "RecordFront", deviceValue: viewModel.settings.recordFront)
      toggleRow("SSH", isOn: $sshEnabled, key: "SshEnabled", deviceValue: viewModel.settings.sshEnabled)
      toggleRow("PID Steering (X70)", isOn: $usePidController, key: "X70UsePidController", deviceValue: viewModel.settings.usePidController, touched: $pidTouched)
      toggleRow("0.11 Model (beta)", isOn: $useSupercomboModel, key: "UseSupercomboModel", deviceValue: viewModel.settings.useSupercomboModel, touched: $modelTouched)
      toggleRow("MADS (beta)", isOn: $madsEnabled, key: "MadsEnabled", deviceValue: viewModel.settings.madsEnabled, touched: $madsTouched)
    } header: {
      Text("Software Settings")
    } footer: {
      Text("Changes are sent to the device immediately. PID/model/MADS take effect on the next drive.")
    }
  }

  // Single-row update section: shows current state and a context-appropriate
  // action button. The updater pulls from the device's configured git remote
  // (GitHub, GitLab, or any server) — we don't assume a host.
  private var updateSection: some View {
    Section {
      // Current branch + remote
      if let branch = viewModel.settings.updaterTargetBranch {
        infoRow("Branch", branch)
      }
      if let branches = viewModel.settings.availableBranches, !branches.isEmpty {
        infoRow("Available", branches)
      }

      // Single status row with action button
      updateStatusRow
    } header: {
      Text("Software Update")
    } footer: {
      Text("Updates are fetched from the device's configured git remote. The app does not auto-download.")
    }
  }

  private var updateStatusRow: some View {
    HStack {
      if viewModel.settings.updateAvailable || viewModel.settings.updaterFetchAvailable {
        Image(systemName: "arrow.down.circle.fill")
          .foregroundStyle(.orange)
        VStack(alignment: .leading, spacing: 2) {
          Text("Update available")
            .font(.system(size: 15, weight: .medium))
          if viewModel.settings.updaterFetchAvailable {
            Text("Downloaded — ready to install")
              .font(.system(size: 12))
              .foregroundStyle(.secondary)
          }
        }
      } else {
        Image(systemName: "checkmark.circle")
          .foregroundStyle(.green)
        Text("Up to date")
          .font(.system(size: 15, weight: .medium))
      }
      Spacer()
      updateActionButton
    }
  }

  private var updateActionButton: some View {
    Group {
      if viewModel.settings.updaterFetchAvailable {
        // Already downloaded — offer install
        Button("Install") {
          viewModel.installUpdate()
          dismiss()
        }
        .buttonStyle(.borderedProminent)
        .tint(.orange)
      } else if viewModel.settings.updateAvailable {
        // Available but not fetched — fetch then it'll be ready
        Button("Download") {
          viewModel.fetchUpdate()
        }
        .buttonStyle(.bordered)
      } else {
        Button("Check") {
          viewModel.checkForUpdate()
        }
        .buttonStyle(.bordered)
      }
    }
  }

  // Wi-Fi: scan for networks, connect, forget current.
  private var wifiSection: some View {
    Section {
      if let current = viewModel.settings.activeWlanSSID {
        HStack {
          Image(systemName: "wifi")
            .foregroundStyle(.green)
          Text(current)
            .font(.system(size: 15, weight: .medium))
          Spacer()
          Button("Forget") {
            showForgetWifiConfirm = true
          }
          .buttonStyle(.bordered)
          .tint(.red)
        }
      } else {
        Text("Not connected")
          .foregroundStyle(.secondary)
      }

      Button {
        viewModel.scanWifi()
      } label: {
        Label("Scan for networks", systemImage: "antenna.radiowaves.left.and.right")
      }

      // Scan results
      if !viewModel.settings.wifiList.isEmpty {
        ForEach(viewModel.settings.wifiList) { network in
          Button {
            if network.needsPassword {
              wifiPasswordInput = ""
              wifiPasswordEntry = network
            } else {
              viewModel.connectWifi(ssid: network.ssid, password: nil)
            }
          } label: {
            HStack {
              Image(systemName: network.needsPassword ? "lock.fill" : "wifi")
                .foregroundStyle(.secondary)
              Text(network.ssid)
                .foregroundStyle(.primary)
              Spacer()
              Image(systemName: "chevron.right")
                .font(.system(size: 12, weight: .semibold))
                .foregroundStyle(.tertiary)
            }
          }
          .buttonStyle(.plain)
        }
      }
    } header: {
      Text("Wi-Fi")
    } footer: {
      if viewModel.settings.wifiList.isEmpty {
        Text("Tap 'Scan for networks' to find nearby Wi-Fi.")
      }
    }
  }

  private var deviceActionsSection: some View {
    Section {
      Button(role: .destructive) {
        showRebootConfirm = true
      } label: {
        Label("Reboot Device", systemImage: "arrow.clockwise")
      }
      .disabled(!viewModel.settings.isOffroad)

      Button(role: .destructive) {
        viewModel.resetCalibration()
      } label: {
        Label("Reset Calibration", systemImage: "scope")
      }
    } header: {
      Text("Device Actions")
    } footer: {
      if !viewModel.settings.isOffroad {
        Text("Reboot is only available when openpilot is off (disabled).")
      }
    }
  }

  private var connectionSection: some View {
    Section("Connection") {
      infoRow("Frames", "\(viewModel.framesReceived)")
      infoRow("FPS", String(format: "%.1f", viewModel.fps))
      infoRow("Auto-Reconnect", viewModel.ble.autoReconnectEnabled ? "On" : "Off")

      Button(role: .destructive) {
        viewModel.ble.forgetDevice()
      } label: {
        Label("Disconnect & Forget", systemImage: "antenna.radiowaves.left.and.right.slash")
      }
    }
  }

  @State private var cacheSizeDisplay = ""

  private var cacheSection: some View {
    Section {
      infoRow("Cached Drives", "\(DeviceService.cacheCount)")
      infoRow("Cache Size", cacheSizeDisplay.isEmpty ? DeviceService.cacheSizeString : cacheSizeDisplay)
      Button(role: .destructive) {
        showClearCacheConfirm = true
      } label: {
        Label("Clear Drive Cache", systemImage: "trash")
      }
      .disabled(DeviceService.cacheCount == 0)
    } header: {
      Text("Drive Analysis Cache")
    } footer: {
      Text("Parsed drive data is cached so re-opening a drive is instant. Clear if storage is low.")
    }
  }

  // MARK: Helpers

  private func toggleRow(_ title: String, isOn: Binding<Bool>, key: String, deviceValue: Bool, touched: Binding<Bool>? = nil) -> some View {
    Toggle(title, isOn: isOn)
      .onChange(of: isOn.wrappedValue) { _, newValue in
        // Only save on a real USER change (not a syncToggles echo overwrite).
        guard newValue != deviceValue else { return }
        // Mark "user owns this toggle" so syncToggles stops echoing it back —
        // otherwise the lagging echo flips the displayed value on/off = "dance".
        touched?.wrappedValue = true
        viewModel.saveToggle(key, value: newValue)
      }
  }

  private func infoRow(_ label: String, _ value: String) -> some View {
    HStack {
      Text(label)
        .foregroundStyle(.secondary)
      Spacer()
      Text(value)
        .font(.system(.body, design: .monospaced))
        .foregroundStyle(.primary)
    }
  }

  private func syncToggles() {
    // All toggles sync from the device echo each frame — EXCEPT PID/0.11 once the
    // user has tapped them (pidTouched/modelTouched), so a lagging echo can't flip
    // the displayed value back and "dance" the toggle on/off.
    experimentalMode = viewModel.settings.experimentalMode
    assistedLaneChange = viewModel.settings.alcEnabled
    quietMode = viewModel.settings.quietMode
    laneDepartureWarning = viewModel.settings.ldwEnabled
    recordDriverCamera = viewModel.settings.recordFront
    sshEnabled = viewModel.settings.sshEnabled
    if !pidTouched { usePidController = viewModel.settings.usePidController }
    if !modelTouched { useSupercomboModel = viewModel.settings.useSupercomboModel }
    if !madsTouched { madsEnabled = viewModel.settings.madsEnabled }
  }
}

/// Password entry sheet for connecting to a password-protected Wi-Fi network.
struct WifiPasswordSheet: View {
  let ssid: String
  let onSubmit: (String?) -> Void
  let onCancel: () -> Void

  @State private var password = ""
  @Environment(\.dismiss) private var dismiss

  var body: some View {
    NavigationStack {
      VStack(spacing: 20) {
        Text("Enter password for")
          .foregroundStyle(.secondary)
        Text(ssid)
          .font(.system(.title3, design: .monospaced).bold())

        SecureField("Password", text: $password)
          .textFieldStyle(.roundedBorder)
          .autocorrectionDisabled()

        Spacer()
      }
      .padding()
      .navigationTitle("Wi-Fi")
      .navigationBarTitleDisplayMode(.inline)
      .toolbar {
        ToolbarItem(placement: .topBarLeading) {
          Button("Cancel") { onCancel(); dismiss() }
        }
        ToolbarItem(placement: .topBarTrailing) {
          Button("Connect") {
            onSubmit(password.isEmpty ? nil : password)
            dismiss()
          }
          .bold()
        }
      }
    }
  }
}
