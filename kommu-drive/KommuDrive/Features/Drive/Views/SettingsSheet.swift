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

  @State private var showRebootConfirm = false
  @State private var wifiPasswordEntry: WifiNetwork? = nil
  @State private var wifiPasswordInput = ""
  @State private var showForgetWifiConfirm = false

  // Model/PID toggles use pending values on the ViewModel so they survive
  // sheet open/close. nil = use device's current value.
  private var pidToggleValue: Bool {
    viewModel.pendingPidToggle ?? viewModel.settings.usePidController
  }
  private var modelToggleValue: Bool {
    viewModel.pendingModelToggle ?? viewModel.settings.useSupercomboModel
  }

  var body: some View {
    NavigationStack {
      List {
        deviceSection
        softwareSettingsSection
        restartRequiredSection
        updateSection
        wifiSection
        deviceActionsSection
        connectionSection
      }
      .navigationTitle("Settings")
      .navigationBarTitleDisplayMode(.inline)
      .toolbar {
        ToolbarItem(placement: .topBarTrailing) {
          Button("Done") { dismiss() }
        }
      }
      .onAppear {
        // Switch the device to streaming settings so the sheet populates.
        viewModel.requestSettings()
        syncToggles()
      }
      .onDisappear {
        // Switch back to visualisation when the sheet closes.
        viewModel.requestVisualisation()
      }
      .onReceive(viewModel.$settings) { _ in syncToggles() }
      .alert("Reboot device?", isPresented: $showRebootConfirm) {
        Button("Reboot", role: .destructive) {
          // Send any pending model/PID toggles BEFORE reboot so they take effect.
          if let pid = viewModel.pendingPidToggle {
            viewModel.saveToggle("X70UsePidController", value: pid)
          }
          if let model = viewModel.pendingModelToggle {
            viewModel.saveToggle("UseSupercomboModel", value: model)
          }
          viewModel.pendingPidToggle = nil
          viewModel.pendingModelToggle = nil
          viewModel.rebootDevice()
          dismiss()
        }
        Button("Cancel", role: .cancel) {}
      } message: {
        if hasPendingModelChanges {
          Text("The device will restart with your model/steering changes applied. This only works when openpilot is disabled.")
        } else {
          Text("The device will restart. This only works when openpilot is disabled.")
        }
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
      toggleRow("Experimental Mode", isOn: $experimentalMode, key: "ConditionalExperimentalMode")
      toggleRow("Assisted Lane Change", isOn: $assistedLaneChange, key: "IsAlcEnabled")
      toggleRow("Lane Departure Warning", isOn: $laneDepartureWarning, key: "IsLdwEnabled")
      toggleRow("Quiet Mode", isOn: $quietMode, key: "QuietMode")
      toggleRow("Record Driver Camera", isOn: $recordDriverCamera, key: "RecordFront")
      toggleRow("SSH", isOn: $sshEnabled, key: "SshEnabled")
    } header: {
      Text("Software Settings")
    } footer: {
      Text("Changes are sent to the device immediately.")
    }
  }

  /// Settings that need a device restart to take effect (modeld, PID controller).
  /// Changes are stored on the ViewModel so they survive sheet open/close.
  private var restartRequiredSection: some View {
    Section {
      restartToggleRow("PID Steering (X70)",
                        isOn: Binding(get: { pidToggleValue },
                                      set: { viewModel.pendingPidToggle = $0 }),
                        deviceValue: viewModel.settings.usePidController)
      restartToggleRow("0.11 Model (beta)",
                        isOn: Binding(get: { modelToggleValue },
                                      set: { viewModel.pendingModelToggle = $0 }),
                        deviceValue: viewModel.settings.useSupercomboModel)

      if hasPendingModelChanges {
        HStack {
          Image(systemName: "exclamationmark.triangle.fill")
            .foregroundStyle(.orange)
          Text("Changes apply after device restart")
            .font(.system(size: 13))
            .foregroundStyle(.orange)
          Spacer()
          Button("Restart Now") {
            showRebootConfirm = true
          }
          .buttonStyle(.borderedProminent)
          .tint(.orange)
        }
      }
    } header: {
      Text("Model & Steering (Requires Restart)")
    } footer: {
      Text("These settings change which driving model runs on the NPU. They only take effect after the device restarts.")
    }
  }

  /// True if any model/PID toggle differs from the device's current state.
  private var hasPendingModelChanges: Bool {
    viewModel.pendingPidToggle != nil || viewModel.pendingModelToggle != nil
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
        dismiss()
      } label: {
        Label("Disconnect & Forget", systemImage: "antenna.radiowaves.left.and.right.slash")
      }
    }
  }

  // MARK: Helpers

  private func toggleRow(_ title: String, isOn: Binding<Bool>, key: String) -> some View {
    Toggle(title, isOn: isOn)
      .onChange(of: isOn.wrappedValue) { _, newValue in
        viewModel.saveToggle(key, value: newValue)
      }
  }

  /// Toggle for settings that need a device restart. Shows the toggle but does
  /// NOT send immediately — it only updates local state. The change is sent to
  /// the device when the user taps "Restart Now" (which sends all pending model
  /// toggles via saveToggle, then triggers reboot).
  private func restartToggleRow(_ title: String, isOn: Binding<Bool>,
                                 deviceValue: Bool) -> some View {
    HStack {
      Toggle(title, isOn: isOn)
      if isOn.wrappedValue != deviceValue {
        Image(systemName: "circle.fill")
          .foregroundStyle(.orange)
          .font(.system(size: 8))
      }
    }
    .onChange(of: isOn.wrappedValue) { _, newValue in
      if newValue != deviceValue {
        AppLog.info("queued toggle=\(newValue) (pending restart)")
      }
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
    // Live toggles — always sync from device (they change on the device side too)
    experimentalMode = viewModel.settings.experimentalMode
    assistedLaneChange = viewModel.settings.alcEnabled
    quietMode = viewModel.settings.quietMode
    laneDepartureWarning = viewModel.settings.ldwEnabled
    recordDriverCamera = viewModel.settings.recordFront
    sshEnabled = viewModel.settings.sshEnabled
    // Model/PID toggles are NOT synced here — they use pending values stored
    // on the ViewModel so they survive sheet open/close.
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
