# KommuDrive

A custom iOS app that connects to an openpilot device (KA2) over Bluetooth LE and
renders live driving data — model path, lane lines, lead car, speeds — in a
perspective-projected view. Built as a replacement for the kommu phone app.

This is **Phase 1**: BLE connection + a simple car/road visualization. It proves
the full pipeline (BLE → msgpack → render) before building out settings, alerts,
and commands.

## Requirements

- Xcode 16+ (built and verified on Xcode 27 / iOS 27 SDK)
- iOS 17.0+ deployment target
- A KA2 device running `selfdrive/appbridged/appbridged.py` (already on the device)

## Open in Xcode

```bash
open kommu-drive/KommuDrive.xcodeproj
```

Select an iPhone simulator and hit ⌘R. To preview the driving view without a
device, open `Features/Drive/Preview/DriveViewPreview.swift` and use ⌥⌘P
(Canvas previews).

## Build from command line

```bash
cd kommu-drive
xcodebuild -project KommuDrive.xcodeproj \
  -scheme KommuDrive \
  -sdk iphonesimulator \
  -destination 'generic/platform=iOS Simulator' \
  -configuration Debug build \
  CODE_SIGNING_ALLOWED=NO
```

## Architecture

Mirrors the folder structure and conventions of the `drivinglog` (DriveStats)
iOS project: feature-folder layout with `App/`, `Core/`, `Features/`, `Services/`,
and `@StateObject`-injected view models.

```
KommuDrive/
├── App/
│   └── KommuDriveApp.swift            @main, owns DriveSessionViewModel, routes between screens
├── Core/
│   ├── Models/
│   │   ├── BLEProtocol.swift          Nordic UART UUIDs, channel IDs, chunk header layout
│   │   ├── DriveModels.swift          DriveFrame, LeadData, PathData, DriveFrameDecoder
│   │   └── DeviceSettings.swift       DeviceSettings (channel 0x02 payload)
│   └── Utilities/
│       ├── AppLog.swift               OSLog wrapper
│       └── RoadProjection.swift       bird's-eye → screen perspective projection
├── Features/
│   └── Drive/
│       ├── ViewModels/
│       │   └── DriveSessionViewModel.swift   @MainActor bridge: BLE → UI state
│       ├── Views/
│       │   ├── ConnectionView.swift          scan + connect screen
│       │   └── DriveView.swift               Canvas-based road renderer + HUD
│       └── Preview/
│           └── DriveViewPreview.swift        synthetic-data SwiftUI previews
├── Services/
│   └── BLE/
│       ├── BLEManager.swift           CoreBluetooth central (scan/connect/notify)
│       ├── ChunkReceiver.swift        reassemble 4-byte-header chunks → messages
│       └── MsgpackDecoder.swift       pure-Swift msgpack decode + encode
└── Resources/
    ├── Info.plist                     NSBluetoothAlwaysUsageDescription
    └── Assets.xcassets/               AccentColor, AppIcon
```

### Data flow

```
[KA2 device]  ─BLE Nordic UART notify─►  BLEManager
                                              │ feed(packet)
                                              ▼
                                         ChunkReceiver ── onMessage(channel, Data) ──►
                                              │
                                              ▼
                                         DriveSessionViewModel (@MainActor)
                                              │ MsgpackDecoder.decodeMap → DriveFrame / DeviceSettings
                                              ▼
                                         @Published latestFrame / settings
                                              │
                                              ▼
                                         DriveView (Canvas) / ConnectionView
```

### BLE protocol (mirrors `selfdrive/appbridged/`)

- **Service:** `6E400001-B5A3-F393-E0A9-E50E24DCCA9E` (Nordic UART)
- **RX char** `...0002...` — phone writes commands (e.g. `{msgType:"curPage"}`)
- **TX char** `...0003...` — device notifies with frames (subscribe to receive)
- **Chunking:** every BLE packet has a 4-byte header `[channel, msgId, totalSegs, segIdx]`
  followed by up to 240 payload bytes. `ChunkReceiver` reassembles.
- **Payload:** msgpack. Channel `0x01` = visualisation (path/lanes/leads/speeds, ~16 Hz),
  channel `0x02` = settings (device status, ~3 Hz).

### Rendering approach

The "3D" road view is **2D bird's-eye data projected with a perspective warp** —
the same technique comma's own UI uses (`selfdrive/ui/onroad/model_renderer.py`).
No SceneKit/Metal needed; a SwiftUI `Canvas` with Core Graphics draws the path,
lanes, lead marker, and ego car at 60fps. See `Core/Utilities/RoadProjection.swift`
for the projection math.

## What Phase 1 does

- ✅ Scan for KA2 devices advertising the Nordic UART service
- ✅ Connect + subscribe to TX notifications
- ✅ Reassemble chunked BLE packets
- ✅ Decode msgpack visualisation + settings payloads (pure Swift, no deps)
- ✅ Render road surface, lane lines, road edges, predicted path corridor
- ✅ Lead car marker at its real distance + lateral offset
- ✅ Ego car icon (color changes with engagement state)
- ✅ HUD: current speed, target speed, lead distance, engagement badge, fps counter
- ✅ **Confidence ball** (rises/turns green as model confidence goes up; red/yellow as it drops) — faithful port of `confidence_ball.py`
- ✅ **Steering limit indicator** (curved arc grows white→orange as lateral accel nears the limit) — faithful port of `torque_bar.py`
- ✅ SwiftUI previews with synthetic data (no device needed)

## Device-side changes (additive, in `appbridged.py`)

The confidence ball and steering limit indicator need data that wasn't on the
BLE stream. `selfdrive/appbridged/appbridged.py` was extended **additively**
(only adds services + fields; nothing existing is removed):

1. `SubMaster` now also reads `controlsState` and `liveParameters`.
2. `send_visualisation_message` now ships three extra fields:
   - `"cf"` — confidence in [0,1]: `(1 - max(brakeDisengageProbs)) * (1 - max(steerOverrideProbs))`
   - `"sl"` — steering-limit fraction in [-1,1] (lateral accel / 3 m/s²)
   - `"vEgo"` — true ego speed (m/s)

These mirror the math in the device UI's `confidence_ball.py` and `torque_bar.py`
so the phone renders identically to the comma screen.

## Privacy fix (`common/params_keys.h`)

`DONT_LOG` was added to `GitBranch`, `GitDiff`, `GitRemote`, `GithubUsername`,
and `GithubSshKeys`. These were previously serialized into every uploaded
`qlog`/`rlog` and shipped to `web.kommu.ai`, exposing the branch name, full
uncommitted `git diff`, fork URL, and GitHub identity. After this change those
keys are no longer written into logs. Requires a C++ rebuild to take effect.

## What's next (Phase 2+)

- [ ] Commands: `saveConfig`, `resetCalibration`, `reboot`, `wifi`, `ssh`
- [ ] Alerts rendering (alertText1/2 + severity colors)
- [ ] Lead relative speed / accel (requires extending `extract_lead`)
- [ ] Settings screen (OpenpilotEnabledToggle, branch selector, WiFi scan)
- [ ] Multi-device pairing + DongleId persistence
- [ ] Landscape orientation + tablet layout

## Data fields now on the BLE stream

| Key | Channel | Field | Source |
|---|---|---|---|
| `f` | vis | frameId | modelV2 |
| `p` | vis | path (x,y) | modelV2.position |
| `l1..l4` | vis | lane lines | modelV2.laneLines |
| `r1,r2` | vis | road edges | modelV2.roadEdges |
| `o`,`t` | vis | leadOne/Two {s,d,y} | radarState |
| `vEgoCluster` | vis | dash speed (m/s) | carState |
| `vCruiseCluster` | vis | target speed (m/s) | carState |
| `vEgo` | vis | true ego speed (m/s) | carState |
| `cf` | vis | confidence [0,1] | modelV2.meta.disengagePredictions |
| `sl` | vis | steering limit [-1,1] | controlsState + liveParameters |
| `enabled`,`state`,`experimentalMode`,`alertText*`,`personality` | vis | engagement + alerts | selfdriveState |
| `m` | vis | isMetric | params |
| `d` | vis | dongleId | params |
| `dongleID`,`gitCommit`,`currentVersion`,`osVersion`,`networkType`,... | settings | device status (~3 Hz) | various |

## License

Private project. All rights reserved.
