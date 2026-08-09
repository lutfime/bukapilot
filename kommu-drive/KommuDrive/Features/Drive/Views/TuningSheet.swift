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

  // MARK: Map corner slowdown

  private var mapCornerSection: some View {
    Section {
      Toggle("Enable map corner slowdown", isOn: $vm.mapDraftEnabled)

      VStack(alignment: .leading, spacing: 4) {
        HStack {
          Text("Corner aggressiveness")
          Spacer()
          Text(String(format: "%.1f m/s²", vm.mapDraftBudget))
            .font(.system(size: 11, design: .monospaced))
            .foregroundStyle(.secondary)
        }
        Slider(value: $vm.mapDraftBudget, in: 1.0...4.0, step: 0.1)
        Text(lowerBudgetLabel(vm.mapDraftBudget))
          .font(.system(size: 10))
          .foregroundStyle(.tertiary)
      }

      VStack(alignment: .leading, spacing: 4) {
        HStack {
          Text("Lookahead")
          Spacer()
          Text(String(format: "%.0f m", vm.mapDraftLookahead))
            .font(.system(size: 11, design: .monospaced))
            .foregroundStyle(.secondary)
        }
        Slider(value: $vm.mapDraftLookahead, in: 100...400, step: 10)
      }

      if vm.mapHasChanges {
        Button {
          vm.saveMapParams()
        } label: {
          HStack {
            Image(systemName: "checkmark.circle.fill")
            Text("Save map corner settings")
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

      // Live status (read-only, reflects what mapd_kommu last published)
      if let p = vm.mapParams {
        HStack {
          Circle()
            .fill(p.valid ? Color.green : Color.gray.opacity(0.3))
            .frame(width: 8, height: 8)
          if p.valid {
            Text("Active — advisory \(String(format: "%.0f km/h", p.vCorner * 3.6))")
              .font(.system(size: 11))
              .foregroundStyle(.secondary)
          } else {
            // Inactive has several causes; without more params we can't tell which from here.
            // SSH and read the status file for the real reason (no params_keys rebuild needed).
            Text("Inactive — run on-road with GPS lock. SSH: cat /tmp/mapd_kommu_status.txt")
              .font(.system(size: 10))
              .foregroundStyle(.tertiary)
          }
        }
      }
    } header: {
      Text("Map Corner Slowdown")
    } footer: {
      Text("Uses OpenStreetMap road geometry to slow the car for upcoming corners. Slowdown-only — can never accelerate. Requires device GPS + internet.")
    }
  }

  private func lowerBudgetLabel(_ b: Double) -> String {
    switch b {
    case ..<1.6: return "very cautious — firm braking for any curve"
    case ..<2.1: return "conservative — slows for most corners"
    case ..<2.8: return "balanced (recommended)"
    case ..<3.5: return "spirited — mild scrub on tight corners"
    default: return "aggressive — rarely triggers"
    }
  }
}
