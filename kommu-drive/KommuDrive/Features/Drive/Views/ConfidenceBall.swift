import SwiftUI

/// Faithful Swift port of `selfdrive/ui/mici/onroad/confidence_ball.py`.
///
/// A vertical ball on the right edge that rises (moves up) and turns green as
/// openpilot's confidence in the scene goes up; falls and turns red as it
/// drops. Driven by `modelV2.meta.disengagePredictions` (field "cf" on BLE).
///
/// Confidence zones (ENGAGED), matching the device UI exactly:
///   > 0.5  → green/cyan gradient   (0,255,204) → (0,255,38)
///   0.2–0.5 → yellow/orange gradient (255,200,0) → (255,115,0)
///   < 0.2  → red gradient          (255,0,21) → (255,0,89)
/// DISENGAGED → dark gray, parked at the bottom.
struct ConfidenceBall: View {

  /// Smoothed confidence in [0, 1]. The VM owns the first-order filter so the
  /// view stays stateless and re-renders purely from this value.
  let smoothedConfidence: Double

  /// true when openpilot is engaged (changes color set).
  let engaged: Bool

  /// Track height available for vertical travel.
  var trackHeight: CGFloat = 220

  /// Ball radius.
  var radius: CGFloat = 22

  var body: some View {
    GeometryReader { geo in
      let h = min(trackHeight, geo.size.height)
      // x in [-0.5, 1]: -0.5 parks at bottom, 1 rises to top.
      // Map to vertical position: bottom (h - r) at x=-0.5, top (r) at x=1.
      let clamped = max(-0.5, min(1.0, smoothedConfidence))
      let span = 1.0 - (-0.5) // 1.5
      let frac = (clamped - (-0.5)) / span
      let ballY = h - (frac * (h - 2 * radius)) - radius

      ZStack {
        // Track guide (subtle vertical line)
        Capsule()
          .fill(Color.white.opacity(engaged ? 0.06 : 0.03))
          .frame(width: 2, height: h - 2 * radius)
          .offset(y: radius)

        // The ball with a vertical gradient.
        let (top, bottom) = ballColors
        Circle()
          .fill(
            LinearGradient(
              colors: [top, bottom],
              startPoint: .top, endPoint: .bottom
            )
          )
          .frame(width: radius * 2, height: radius * 2)
          .shadow(color: top.opacity(0.5), radius: 6, y: 0)
          .offset(y: ballY - h / 2 + radius)
      }
      .frame(width: radius * 2 + 8, height: h)
      .frame(maxHeight: h, alignment: .top)
    }
    .frame(width: radius * 2 + 12, height: trackHeight)
    .animation(.easeOut(duration: 0.12), value: smoothedConfidence)
  }

  /// Returns (topColor, bottomColor) matching confidence_ball.py color zones.
  private var ballColors: (Color, Color) {
    if !engaged {
      // DISENGAGED → dark gray parked at bottom.
      return (Color(red: 50/255, green: 50/255, blue: 50/255),
              Color(red: 13/255, green: 13/255, blue: 13/255))
    }
    if smoothedConfidence > 0.5 {
      return (Color(red: 0/255, green: 255/255, blue: 204/255),
              Color(red: 0/255, green: 255/255, blue: 38/255))
    } else if smoothedConfidence > 0.2 {
      return (Color(red: 255/255, green: 200/255, blue: 0/255),
              Color(red: 255/255, green: 115/255, blue: 0/255))
    } else {
      return (Color(red: 255/255, green: 0/255, blue: 21/255),
              Color(red: 255/255, green: 0/255, blue: 89/255))
    }
  }
}
