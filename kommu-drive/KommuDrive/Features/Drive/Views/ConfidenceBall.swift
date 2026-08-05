import SwiftUI

/// Faithful Swift port of comma 4's confidence indicator.
///
/// A sleek glowing status dot on the right side of the road view.
/// - Engaged & High Confidence (>0.5): Vibrant glowing Green (#00FF80)
/// - Engaged & Medium Confidence (0.2–0.5): Warning Amber/Orange (#FFB300)
/// - Engaged & Low Confidence (<0.2): Alert Red (#FF2A4B)
/// - Disengaged: Subtle Slate Gray
struct ConfidenceBall: View {

  /// Smoothed confidence in [0, 1].
  let smoothedConfidence: Double

  /// true when openpilot is engaged.
  let engaged: Bool

  /// Track height available for subtle vertical motion.
  var trackHeight: CGFloat = 100

  /// Ball radius.
  var radius: CGFloat = 12

  var body: some View {
    let clamped = max(0.0, min(1.0, smoothedConfidence))
    let statusColor = ballColor(confidence: clamped, engaged: engaged)

    ZStack {
      // Outer ambient glow ring (comma 4 halo effect)
      Circle()
        .fill(statusColor.opacity(engaged ? 0.35 : 0.05))
        .frame(width: radius * 3.2, height: radius * 3.2)
        .blur(radius: 6)

      // Main glowing status ball
      Circle()
        .fill(statusColor)
        .frame(width: radius * 2, height: radius * 2)
        .shadow(color: statusColor.opacity(engaged ? 0.8 : 0.2), radius: 8, x: 0, y: 0)
        .overlay(
          Circle()
            .stroke(Color.white.opacity(engaged ? 0.6 : 0.2), lineWidth: 1.5)
        )
    }
    .frame(width: radius * 3.5, height: trackHeight)
    .animation(.easeInOut(duration: 0.2), value: smoothedConfidence)
    .animation(.easeInOut(duration: 0.2), value: engaged)
  }

  private func ballColor(confidence: Double, engaged: Bool) -> Color {
    guard engaged else {
      return Color(red: 90/255, green: 100/255, blue: 115/255)
    }
    if confidence > 0.5 {
      return Color(red: 0/255, green: 235/255, blue: 130/255)  // Vibrant Comma 4 Green
    } else if confidence > 0.2 {
      return Color(red: 255/255, green: 175/255, blue: 0/255)  // Comma 4 Warning Amber
    } else {
      return Color(red: 255/255, green: 42/255, blue: 75/255)   // Alert Red
    }
  }
}
