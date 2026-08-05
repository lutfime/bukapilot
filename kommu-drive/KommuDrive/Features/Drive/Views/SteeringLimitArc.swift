import SwiftUI

/// Faithful Swift port of the steering-limit indicator from
/// `selfdrive/ui/mici/onroad/torque_bar.py`.
///
/// A curved arc above the ego car that **grows** as the lateral acceleration
/// nears its limit (~3 m/s²). When small (|value| < 0.5) it shows just a gray
/// dot; as |value| grows toward 1.0 the arc grows taller and fades from white
/// through yellow to orange to warn the driver steering is near its limit.
///
/// `value` in [-1, 1]: sign = direction of torque demand, magnitude = fraction
/// of the lateral-accel budget being used. 0 = no demand, ±1 = at the limit.
struct SteeringLimitArc: View {

  let value: Double           // smoothed torque/limit fraction, [-1, 1]
  let engaged: Bool           // hidden when disengaged

  var body: some View {
    Canvas { ctx, size in
      drawArc(ctx: &ctx, size: size)
    }
    .frame(width: 200, height: 90)
    .opacity(engaged ? 1 : 0)
    .animation(.easeOut(duration: 0.1), value: value)
  }

  private func drawArc(ctx: inout GraphicsContext, size: CGSize) {
    let mag = abs(value)

    // Below 0.5 → just a small center dot (matches torque_bar.py:259-262).
    if mag < 0.5 {
      let dot = CGRect(x: size.width / 2 - 5, y: size.height - 18, width: 10, height: 10)
      ctx.fill(Path(ellipseIn: dot),
               with: .color(.white.opacity(0.7 * (engaged ? 1 : 0))))
      return
    }

    // Geometry — matches the device's huge-radius arc (1200 px) centered below
    // the bottom of its rect, so the visible portion is a shallow cap.
    let lineOffset: CGFloat = interpolate(mag, inMin: 0.5, inMax: 1.0, outMin: 22, outMax: 26)
    let lineHeight: CGFloat  = interpolate(mag, inMin: 0.5, inMax: 1.0, outMin: 14, outMax: 56)
    let radius: CGFloat = 1200
    let angleSpan: CGFloat = 12.7   // TORQUE_ANGLE_SPAN

    let cx = size.width / 2
    // center the arc below the canvas so only the cap shows at the top
    let cy = size.height + radius - lineOffset
    let midR = radius + lineHeight / 2

    // Background arc (full angle span, faded white).
    let bgAlpha = interpolate(mag, inMin: 0.5, inMax: 1.0, outMin: 0.25, outMax: 0.5)
    let bgSpan = angleSpan
    let bgStart = Angle.degrees(Double(-90 - bgSpan / 2))
    let bgEnd   = Angle.degrees(Double(-90 + bgSpan / 2))

    let bgPath = arcPath(center: CGPoint(x: cx, y: cy), radius: midR,
                         thickness: lineHeight, start: bgStart, end: bgEnd)
    ctx.stroke(bgPath,
               with: .color(.white.opacity(bgAlpha * (engaged ? 1 : 0.15))),
               lineWidth: lineHeight)

    // Active arc: grows from center toward the demand direction.
    // value > 0 → sweep right, value < 0 → sweep left.
    let halfSpan = bgSpan / 2 * CGFloat(value)  // signed
    let actStart = Angle.degrees(-90)
    let actEnd   = Angle.degrees(Double(-90 + halfSpan))

    // Color: white → yellow → orange as magnitude approaches 1.
    // Uses the same ramp as torque_bar.py (fade starts at 0.75).
    let color = SteeringLimitArc.warnColor(magnitude: mag, engaged: engaged)

    let actPath = arcPath(center: CGPoint(x: cx, y: cy), radius: midR,
                          thickness: lineHeight,
                          start: actStart, end: actEnd)
    ctx.stroke(actPath, with: .color(color), lineWidth: lineHeight)
  }

  // MARK: Geometry helpers

  /// Build a path tracing the centerline of an arc. We stroke it with the
  /// thickness as the line width, which is simpler than the device's polygon
  /// construction and visually equivalent for these shallow caps.
  private func arcPath(center: CGPoint, radius: CGFloat, thickness: CGFloat,
                       start: Angle, end: Angle) -> Path {
    var p = Path()
    p.addArc(center: center, radius: radius, startAngle: start,
             endAngle: end, clockwise: false)
    return p
  }

  private func interpolate(_ v: Double, inMin: Double, inMax: Double,
                           outMin: CGFloat, outMax: CGFloat) -> CGFloat {
    let t = max(0, min(1, (v - inMin) / (inMax - inMin)))
    return outMin + (outMax - outMin) * CGFloat(t)
  }
}

extension SteeringLimitArc {
  /// Returns a single color approximating the device's white→yellow→orange ramp.
  /// Uses SwiftUI's Color blending via opacity since Color components aren't
  /// directly readable. For our shallow arcs the visual result matches the
  /// device closely.
  static func warnColor(magnitude: Double, engaged: Bool) -> Color {
    let t = max(0, min(1, (magnitude - 0.75) * 4))
    if t <= 0 {
      return Color.white.opacity(0.9 * (engaged ? 1 : 0.35))
    }
    // Blend white → orange by overlaying with opacity.
    return Color(red: 1.0, green: 1.0 - 0.55 * t, blue: 1.0 - 1.0 * t)
      .opacity(0.9 * (engaged ? 1 : 0.35))
  }
}
