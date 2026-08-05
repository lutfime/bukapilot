## Hybrid Renderer: SceneKit road + SwiftUI HUD

### Architecture

```
ZStack {
  ┌─────────────────────────────────────┐
  │ SwiftUI HUD Overlay (top layer)      │  ← speed, target, lead badge,
  │ - Top bar: speed, target, gear btn   │    confidence ball, steering arc
  │ - Confidence ball (bottom-right)     │    Fixed screen positions,
  │ - Steering arc (bottom-center)       │    SwiftUI handles layout fine
  │ - Lead badge (top-left)              │
  └─────────────────────────────────────┘
  ┌─────────────────────────────────────┐
  │ SceneKit Road View (bottom layer)    │  ← 3D perspective camera
  │ - Road surface plane                 │    Lane ribbons are real 3D
  │ - Lane line ribbons (4) — real width │    geometry (meters, not pixels)
  │ - Road edge ribbons (2)              │    so foreshortening is automatic
  │ - Path corridor (green gradient)     │    and lane width is always correct
  │ - Detected cars (pool of 3, animated)│
  │ - Lead car (animated position/alpha) │
  │ - Ego car (above steering arc)       │
  └─────────────────────────────────────┘
}
```

The two layers don't reference each other's positions — SceneKit owns its 3D world, SwiftUI owns its screen-space HUD. No ZStack drift.

### New files

1. **`Features/Drive/Rendering/DriveSceneRenderer.swift`** — `@MainActor` class owning the `SCNScene`, all road nodes, and an `update(frame:sceneSize:)` method called per frame. Builds lane ribbons as 3D geometry (SCNNode with custom geometry vertices), positions cars/lead/ego in world space, animates position changes via `SCNTransaction`.

2. **`Features/Drive/Rendering/DriveSceneView.swift`** — `UIViewRepresentable` wrapping `SCNView`. Forwards frames from the ViewModel to the renderer. Handles scene setup (camera, lighting, tap-to-focus off, antialiasing).

### Modified files

3. **`Features/Drive/Views/DriveView.swift`** — rewritten as a simple ZStack: `DriveSceneView` on the bottom, SwiftUI HUD overlay on top. All Canvas drawing code removed. HUD elements repositioned:
   - Top: current speed (left), engagement badge (center), target speed + lead badge + gear (right)
   - Bottom-center: steering arc (SwiftUI, unchanged from current)
   - Bottom-right: confidence ball (SwiftUI, smaller, at very bottom)
   - FPS counter: small text top-right corner

4. **`ConfidenceBall.swift`** and **`SteeringLimitArc.swift`** — kept as-is (SwiftUI views), just repositioned in the new layout.

### Deleted files
5. **`RoadProjection.swift`** — replaced by SceneKit's camera projection. The 2D bird's-eye math is no longer needed because the 3D camera handles foreshortening natively.

### SceneKit scene design

**Coordinate system:** SceneKit standard (Y up, -Z forward into the screen)
- Model `x` (meters forward) → SceneKit `-Z`
- Model `y` (meters left) → SceneKit `-X`
- Road elements sit on the Y=0 ground plane

**Camera:** Perspective camera at `(0, 8, 8)` looking at `(0, 0, -30)`, FOV ~40°. This gives the same "looking forward and slightly down" perspective as comma's UI. Near/far clip: 1m / 200m.

**Lane ribbons:** Built as triangle-strip geometry. Each ribbon is a flat mesh on the ground plane with vertices offset laterally by the lane width (0.12 or 0.16 × lane width in meters). Since it's real 3D geometry, perspective foreshortening is automatic — no manual width calculation. Color = green `(0, 1, 0.25)` with alpha = probability.

**Path corridor:** Same approach — a flat ribbon following the predicted path, with a green gradient material (brighter near, fading far).

**Lead car:** An `SCNBox` or simple car-shaped node. Position updates animate via `SCNTransaction(animationDuration: 0.1)` — smooth easing when the lead accelerates/decelerates. Opacity animates 0→1 when lead appears, 1→0 when it disappears.

**Detected cars:** Pool of 3 `SCNBox` nodes. Each frame, match incoming cars to pooled nodes (nearest-match), animate position changes, fade unused nodes to 0.

**Ego car:** Fixed position at `(0, 0, 2)` (slightly toward camera), rendered as a simple car icon node. Above where the steering arc sits in the HUD overlay.

### Animations (the "real driving feel")
- **Lead stopping:** `SCNTransaction { duration = 0.1; leadNode.position = newPos }` — smooth ease toward shorter distance
- **Lead accelerating:** same mechanism, eases toward longer distance
- **Lead appearing/disappearing:** `SCNTransaction { duration = 0.3; leadNode.opacity = 0 or 1 }`
- **New detected car:** unused pool node fades in at the new position
- **Lane lines updating:** geometry vertices update each frame, SceneKit interpolates the mesh smoothly

### What stays unchanged
- `DriveSessionViewModel` — still feeds `latestFrame`, `smoothedConfidence`, `smoothedSteeringLimit`
- `DriveModels.swift`, `DeviceSettings.swift`, BLE layer, `SettingsSheet.swift`, `ConnectionView.swift`
- All filtering logic

### Implementation order
1. Create `DriveSceneRenderer.swift` (scene + camera + node builders + update method)
2. Create `DriveSceneView.swift` (UIViewRepresentable wrapper)
3. Rewrite `DriveView.swift` (ZStack: SceneView + HUD overlay)
4. Delete `RoadProjection.swift`, remove Canvas code
5. Update preview with synthetic data
6. Build + verify