import SwiftUI

/// Device log viewer. Uses LogsViewModel for all logic — SSH fetch, JSON parsing,
/// category switching. The view just observes.
struct LogsView: View {
  @ObservedObject var viewModel: DriveSessionViewModel
  @StateObject private var vm = LogsViewModel()

  var body: some View {
    NavigationStack {
      VStack(spacing: 0) {
        // Category picker
        Picker("Category", selection: Binding(
          get: { vm.selectedCategory },
          set: { vm.selectCategory($0) }
        )) {
          ForEach(LogsViewModel.LogCategory.allCases, id: \.self) { cat in
            Text(cat.rawValue).tag(cat)
          }
        }
        .pickerStyle(.segmented)
        .padding(.horizontal, 12)
        .padding(.top, 8)

        // Toolbar: refresh + copy
        HStack {
          Button {
            Task { await vm.fetchLogs() }
          } label: {
            Label("Refresh", systemImage: "arrow.clockwise")
          }
          .disabled(vm.loading)

          Spacer()

          if vm.loading { ProgressView().scaleEffect(0.7) }

          if !vm.logs.isEmpty {
            Button {
              UIPasteboard.general.string = vm.logs
            } label: {
              Image(systemName: "doc.on.doc")
            }
          }
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 6)

        // Log content
        if let error = vm.error {
          Spacer()
          VStack(spacing: 12) {
            Image(systemName: "exclamationmark.triangle")
              .font(.system(size: 32))
              .foregroundStyle(.orange)
            Text(error)
              .font(.system(size: 13, design: .monospaced))
              .foregroundStyle(.secondary)
              .multilineTextAlignment(.center)
              .padding(.horizontal, 24)
          }
          Spacer()
        } else if vm.logs.isEmpty && !vm.loading {
          Spacer()
          VStack(spacing: 12) {
            Image(systemName: "doc.text.magnifyingglass")
              .font(.system(size: 32))
              .foregroundStyle(.secondary)
            Text("No logs yet")
              .font(.system(size: 14))
              .foregroundStyle(.secondary)
            Text("Tap Refresh to fetch from device")
              .font(.system(size: 12))
              .foregroundStyle(.tertiary)
          }
          Spacer()
        } else {
          ScrollView {
            Text(vm.logs)
              .font(.system(size: 11, design: .monospaced))
              .frame(maxWidth: .infinity, alignment: .leading)
              .padding(10)
              .textSelection(.enabled)
          }
        }
      }
      .navigationTitle("Logs")
      .navigationBarTitleDisplayMode(.inline)
      .onReceive(viewModel.$settings) { vm.updateSettings($0) }
      .task {
        vm.updateSettings(viewModel.settings)
        await vm.fetchLogs()
      }
    }
  }
}
