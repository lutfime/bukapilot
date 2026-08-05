import CoreGraphics

/// Bird's-eye → screen projection for the driving view.
///
/// This is the "fake 3D" approach: the model data is 2D (meters forward x,
/// meters lateral y) and we project it onto the screen with a simple
/// perspective foreshortening. This matches how comma's own openpilot UI works
/// (`selfdrive/ui/onroad/model_renderer.py`) and gives the depth look without a
/// 3D engine.
///
/// Convention used by openpilot model data:
///   - x = meters ahead of the car (positive forward)
///   - y = meters to the LEFT of the car (positive left)
///
/// Screen: x grows right, y grows DOWN. Horizon is near the top.
struct RoadProjection {

  /// View geometry (set per-frame from the Canvas size).
  let size: CGSize

  /// Tunable projection parameters.
  let horizonY: CGFloat       // distance from top to horizon line
  let bottomY: CGFloat        // distance from top to where the car sits (bottom area)
  let nearMeters: CGFloat     // model x at the bottom of the screen (closest visible)
  let farMeters: CGFloat      // model x at the horizon (furthest visible)
  let pixelsPerMeterAtBottom: CGFloat // lateral scale at the near plane

  init(size: CGSize,
       horizonY: CGFloat = 0.18,
       bottomY: CGFloat? = nil,
       nearMeters: CGFloat = 4.0,
       farMeters: CGFloat = 100.0,
       pixelsPerMeterAtBottom: CGFloat = 18.0) {
    self.size = size
    self.horizonY = horizonY * size.height
    self.bottomY = (bottomY ?? 0.92) * size.height
    self.nearMeters = nearMeters
    self.farMeters = farMeters
    self.pixelsPerMeterAtBottom = pixelsPerMeterAtBottom * (size.width / 390.0)
  }

  /// Project a (xForwardMeters, yLeftMeters) point to screen coordinates.
  /// Returns nil if the point is behind the near plane.
  func project(xForward: Double, yLeft: Double) -> CGPoint? {
    let x = CGFloat(xForward)
    guard x >= nearMeters else { return nil }
    // Depth fraction in [0,1]: 0 = near (bottom), 1 = far (horizon).
    let t = clamp((x - nearMeters) / (farMeters - nearMeters), lo: 0, hi: 1)
    // Screen Y: near points at bottomY, far points at horizonY.
    let screenY = bottomY - t * (bottomY - horizonY)
    // Perspective lateral scale: shrinks toward the horizon.
    let scale = lerp(pixelsPerMeterAtBottom, pixelsPerMeterAtBottom * 0.18, t)
    let screenX = size.width * 0.5 - CGFloat(yLeft) * scale
    return CGPoint(x: screenX, y: screenY)
  }

  private func clamp(_ v: CGFloat, lo: CGFloat, hi: CGFloat) -> CGFloat {
    Swift.max(lo, Swift.min(hi, v))
  }
  private func lerp(_ a: CGFloat, _ b: CGFloat, _ t: CGFloat) -> CGFloat {
    a + (b - a) * t
  }
}
