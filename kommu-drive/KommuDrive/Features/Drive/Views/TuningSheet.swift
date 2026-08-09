import SwiftUI

/// SSH-based PID tuning panel. Uses TuningViewModel for all logic.
struct TuningSheet: View {
  @ObservedObject var viewModel: DriveSessionViewModel
  @StateObject private var vm = TuningViewModel()
  @State private var manualIP: String = ""
  @State private var showManualEntry: Bool = false

  var body: some View {
    NavigationStack {
      List {
        if !vm.isConnected {
          sshSetupSection
        } else {
          ForEach(vm.sections) { section in
            tuningSection(section)
          }
          if !vm.sections.isEmpty {
            saveSection
          }
          mapCornerSection
        }
      }
      .navigationTitle("PID Tuning")
      .navigationBarTitleDisplayMode(.inline)
    }
    .onAppear {
      vm.updateSettings(viewModel.settings)
      Task { await vm.connect() }
    }
    .onDisappear { vm.disconnect() }
  }

  // MARK: SSH setup

  private var sshSetupSection: some View {
    Section {
      VStack(spacing: 12) {
        if vm.status.isEmpty {
          VStack(spacing: 6) {
            ProgressView()
            Text("Connecting to \(vm.hostLabel)…")
              .font(.system(size: 13))
            Text("WiFi: \(vm.currentSSID) → \(vm.resolvedIP)")
              .font(.system(size: 11, design: .monospaced))
              .foregroundStyle(.tertiary)
          }
        } else {
          Image(systemName: "exclamationmark.triangle.fill")
            .foregroundStyle(.orange)
          Text(vm.status)
            .font(.system(size: 11, design: .monospaced))
            .foregroundStyle(.secondary)
            .multilineTextAlignment(.leading)
            .frame(maxWidth: .infinity, alignment: .leading)
            .textSelection(.enabled)

          if showManualEntry {
            VStack(spacing: 8) {
              Text("Enter device IP manually")
                .font(.system(size: 12, weight: .medium))
              TextField("192.168.0.9", text: $manualIP)
                .font(.system(size: 14, design: .monospaced))
                .textFieldStyle(.roundedBorder)
                .keyboardType(.decimalPad)
                .autocorrectionDisabled()
              Button("Connect") {
                let key = HotspotDetector.isOnDeviceHotspot ? "hotspot" : (HotspotDetector.currentSSID ?? "manual")
                DeviceService.cacheIP(manualIP, forKey: key)
                vm.updateSettings(viewModel.settings)
                showManualEntry = false
                Task { await vm.connect() }
              }
              .buttonStyle(.borderedProminent)
              .disabled(manualIP.isEmpty)
            }
          } else {
            Button("Enter IP manually") {
              showManualEntry = true
            }
            .buttonStyle(.bordered)
          }

          Button("Copy error") {
            UIPasteboard.general.string = vm.status
          }
          .buttonStyle(.bordered)
          .tint(.secondary)
          Button("Retry") {
            vm.updateSettings(viewModel.settings)
            Task { await vm.connect() }
          }
          .buttonStyle(.bordered)
        }
      }
      .frame(maxWidth: .infinity)
      .padding(.vertical, 20)
    } header: {
      Text("SSH Connection")
    }
  }

  // MARK: Tuning sections

  private func tuningSection(_ section: DeviceService.TuningSection) -> some View {
    Section {
      ForEach(section.lines) { line in
        VStack(alignment: .leading, spacing: 3) {
          HStack {
            Text(line.label)
              .font(.system(size: 11, weight: .medium))
            Spacer()
            Text("L\(line.lineNum)")
              .font(.system(size: 9, design: .monospaced))
              .foregroundStyle(.tertiary)
          }
          TextField("value", text: Binding(
            get: { vm.editedLines[line.lineNum] ?? line.value },
            set: { vm.editedLines[line.lineNum] = $0 }
          ), axis: .horizontal)
          .font(.system(size: 11, design: .monospaced))
          .textFieldStyle(.roundedBorder)
          .autocorrectionDisabled()
        }
        .padding(.vertical, 2)
      }
    } header: {
      Text(section.name)
    }
  }

  private var saveSection: some View {
    Section {
      let changed = vm.editedLines.filter { lineNum, newValue in
        let original = vm.sections.flatMap(\.lines).first(where: { $0.lineNum == lineNum })?.value
        return newValue != original && !newValue.isEmpty
      }
      Button {
        vm.saveChanges()
      } label: {
        HStack {
          Image(systemName: "checkmark.circle.fill")
          Text("Save \(changed.count) change\(changed.count == 1 ? "" : "s")")
        }
      }
      .buttonStyle(.borderedProminent)
      .tint(.green)
      .disabled(changed.isEmpty)

      if !vm.saveResult.isEmpty {
        VStack(alignment: .leading, spacing: 6) {
          Text(vm.saveResult)
            .font(.system(size: 11, design: .monospaced))
            .foregroundStyle(vm.saveResult.hasPrefix("✓") ? .green : .red)
            .textSelection(.enabled)
          if vm.saveResult.contains("✗") {
            Button("Copy error") {
              UIPasteboard.general.string = vm.saveResult
            }
            .buttonStyle(.bordered)
            .tint(.secondary)
          }
        }
      }
    } header: {
      Text("Apply")
    } footer: {
      Text("Changes apply on the next drive. Each save is syntax-checked before committing.")
    }
  }

  // MARK: Curve slowdown (CSC — model curvature based)

  private var mapCornerSection: some View {
    Section {
      Toggle("Enable curve slowdown", isOn: $vm.mapDraftEnabled)

      // Profile picker (CSC)
      Picker("Profile", selection: $vm.mapDraftProfile) {
        Text("Gentle").tag(0)
        Text("Standard").tag(1)
        Text("Sport").tag(2)
        Text("Auto").tag(3)
      }
      .pickerStyle(.segmented)
      Text(profileDescription(vm.mapDraftProfile))
        .font(.system(size: 10))
        .foregroundStyle(.tertiary)

      if vm.mapHasChanges {
        Button {
          vm.saveMapParams()
        } label: {
          HStack {
            Image(systemName: "checkmark.circle.fill")
            Text("Save curve settings")
          }
        }
        .buttonStyle(.borderedProminent)
        .tint(.blue)
      }

      if !vm.mapSaveResult.isEmpty {
        Text(vm.mapSaveResult)
          .font(.system(size: 11, design: .monospaced))
          .foregroundStyle(vm.mapSaveResult.contains("✓") ? .green : .red)
          .textSelection(.enabled)
      }
    } header: {
      Text("Curve Slowdown")
    } footer: {
      Text("Uses the driving model's predicted road curvature to slow for upcoming corners. Slowdown-only — can never accelerate. No internet or GPS needed.")
    }
  }

  private func profileDescription(_ p: Int) -> String {
    switch p {
    case 0: return "Fixed 1.5 m/s² — cautious, slows for most corners"
    case 1: return "Fixed 2.0 m/s² — balanced (recommended)"
    case 2: return "Self-tuning — grows toward the steering limit, backs off on saturation"
    case 3: return "Learns your driving — adapts the budget from how you take corners"
    default: return ""
    }
  }
}
