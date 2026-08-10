# iOS App Coding Standards (KommuDrive)

## MVVM — ALWAYS use a ViewModel

Every view that does logic (SSH, state, network) MUST have a dedicated ViewModel.
Never put `DeviceService`, SSH commands, or business logic directly in a SwiftUI `View`.

**Pattern (follow exactly):**
```swift
// ViewModel
final class SomeViewModel: ObservableObject {
  @Published var data: String = ""
  @Published var loading = false
  @Published var error: String?

  private let service = DeviceService()
  private var settings: DeviceSettings = .empty

  @MainActor
  func updateSettings(_ s: DeviceSettings) {
    settings = s
  }

  @MainActor
  func fetchData() async {
    loading = true
    error = nil

    do {
      try await service.connectIfNeeded(hotspotIp: settings.hotspotIp, localIP: settings.localIP)
      let result = try await service.execute("some command")
      data = result
    } catch let err {
      error = err.localizedDescription
    }

    loading = false
  }
}

// View
struct SomeView: View {
  @ObservedObject var viewModel: DriveSessionViewModel  // session for settings
  @StateObject private var vm = SomeViewModel()          // own ViewModel

  var body: some View {
    // ... uses vm.data, vm.loading, vm.error
    .onReceive(viewModel.$settings) { vm.updateSettings($0) }
    .task { vm.updateSettings(viewModel.settings); await vm.fetchData() }
  }
}
```

## @MainActor — on FUNCTIONS ONLY, never on the class

NEVER put `@MainActor` on the class declaration. It forces everything (including
network/SSH calls) onto the main thread, blocking the UI.

Put `@MainActor` on **individual functions** that mutate `@Published` state.
The `await` calls inside them automatically suspend and run on their own executor.

**CORRECT:**
```swift
final class MyVM: ObservableObject {
  @MainActor
  func fetchData() async {
    loading = true                          // runs on main (synchronous)
    let result = await service.execute()    // suspends, runs on background
    data = result                           // back on main
    loading = false
  }
}
```

**WRONG (do NOT do this):**
```swift
@MainActor                    // ← NEVER on class
final class MyVM: ObservableObject { ... }

func fetchData() async {
  await MainActor.run {        // ← NEVER wrap manually
    loading = true
  }
  await setloading(false)      // ← NEVER use wrapper functions
}

@MainActor
private func setloading(_ val: Bool) {  // ← NEVER create these
  loading = val
}
```

**Key insight:** `@MainActor` on a func means the synchronous code runs on main,
but `await` calls inside it automatically hop off-main and back. No manual
`MainActor.run` wrappers needed. No helper functions needed.

## Catch bindings — rename to avoid shadowing `error` property

If the ViewModel has a `@Published var error: String?`, the catch clause
`catch { error = ... }` won't compile because `error` refers to the local
Swift Error, not the property. Use `catch let err`:

```swift
} catch let err {
  error = err.localizedDescription  // 'error' = property, 'err' = caught error
}
```

## Category/segment switching — use ViewModel method

For segmented controls that trigger a fetch, don't use `.onChange` in the View.
Add a method to the ViewModel:

```swift
@MainActor
func selectCategory(_ cat: Category) {
  selectedCategory = cat
  Task { await fetchData() }
}
```

Bind in the View:
```swift
Picker("Category", selection: Binding(
  get: { vm.selectedCategory },
  set: { vm.selectCategory($0) }
))
```

## Settings flow

ViewModels that need SSH need device settings (IP addresses). Use `@ObservedObject var viewModel: DriveSessionViewModel`
from RootView to get settings, then feed them to the local ViewModel:
```swift
.onReceive(viewModel.$settings) { vm.updateSettings($0) }
```

## Xcode project — auto file discovery

The project uses `PBXFileSystemSynchronizedRootGroup` — new `.swift` files in the
`KommuDrive/` directory tree are picked up automatically on build. No need to edit
the `.pbxproj` manually.

## DeviceService API

```swift
let service = DeviceService()
try await service.connectIfNeeded(hotspotIp: String?, localIP: String?)
let result = try await service.execute("shell command")
service.connectionError  // String?
service.disconnect()

DeviceService.resolveHost(hotspotIp: String?, localIP: String?) -> String?
DeviceService.hostLabel(ssid: String?) -> String
```
