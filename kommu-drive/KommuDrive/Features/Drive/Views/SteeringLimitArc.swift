import SwiftUI

/// Faithful Swift port of openpilot / comma 4's steering limit torque bar (`torque_bar.py`).
///
/// A curved arc track drawn with rounded end caps (`lineCap: .round`) for both
/// the background track and the active torque bar.
/// When steering torque demand nears the limit (>80%), the active stroke width
/// expands **2x** (14pt -> 28pt) and transitions to warning orange.
struct SteeringLimitArc: View {

  /// Smoothed torque demand / limit fraction in [-1, 1].
  /// 0 = center / neutral, +1 = max right demand, -1 = max left demand.
  let value: Double

  /// true when openpilot is engaged.
  let engaged: Bool

  var body: some View {
    Canvas { ctx, size in
      drawTorqueBar(ctx: &ctx, size: size)
    }
    .frame(width: 220, height: 48)
    .opacity(engaged ? 1 : 0)
    .animation(.easeInOut(duration: 0.1), value: value)
  }

  private func drawTorqueBar(ctx: inout GraphicsContext, size: CGSize) {
    guard engaged else { return }

    let baseWidth: CGFloat = 14.0
    let radius: CGFloat = 900 // Shallow arc radius

    let cx = size.width / 2
    let cy = size.height + radius - 20
    let arcRadius = radius + baseWidth / 2

    let angleSpan: Double = 14.0 // Total degrees span across track
    let halfSpan = angleSpan / 2.0

    // 1. Draw Full Background Track (lineCap: .round)
    let bgStart = Angle.degrees(-90.0 - halfSpan)
    let bgEnd = Angle.degrees(-90.0 + halfSpan)

    var bgPath = Path()
    bgPath.addArc(center: CGPoint(x: cx, y: cy), radius: arcRadius,
                  startAngle: bgStart, endAngle: bgEnd, clockwise: false)

    let bgStrokeStyle = StrokeStyle(lineWidth: baseWidth, lineCap: .round, lineJoin: .round)
    ctx.stroke(bgPath, with: .color(Color.white.opacity(0.22)), style: bgStrokeStyle)

    // 2. Draw Active Torque Demand Bar (lineCap: .round)
    let mag = abs(value)

    if mag > 0.04 {
      // Sweeps from center (-90deg) left or right based on value
      let signedSpan = halfSpan * value
      let actStart = Angle.degrees(-90.0)
      let actEnd = Angle.degrees(-90.0 + signedSpan)

      var actPath = Path()
      let isSweepRight = value > 0
      actPath.addArc(
        center: CGPoint(x: cx, y: cy),
        radius: arcRadius,
        startAngle: isSweepRight ? actStart : actEnd,
        endAngle: isSweepRight ? actEnd : actStart,
        clockwise: false
      )

      // Thickness expands 2x when nearing 80%+ limit: 14pt -> 28pt (2x width!)
      let actLineWidth: CGFloat
      if mag < 0.80 {
        actLineWidth = baseWidth
      } else {
        let t = min(1.0, (mag - 0.80) / 0.20)
        actLineWidth = baseWidth * (1.0 + 1.0 * t) // 14pt -> 28pt (2x)
      }

      let warnColor = SteeringLimitArc.pillColor(magnitude: mag)
      let actStrokeStyle = StrokeStyle(lineWidth: actLineWidth, lineCap: .round, lineJoin: .round)
      ctx.stroke(actPath, with: .color(warnColor), style: actStrokeStyle)
    } else {
      // Neutral position: crisp center dot
      let centerDot = CGRect(x: cx - 5, y: (cy - arcRadius) - 5, width: 10, height: 10)
      ctx.fill(Path(ellipseIn: centerDot), with: .color(Color.white.opacity(0.90)))
    }
  }

  private static func pillColor(magnitude: Double) -> Color {
    if magnitude < 0.80 {
      return Color.white
    }
    let t = min(1.0, (magnitude - 0.80) / 0.20)
    // Fade from solid white to bright warning orange (#FF7E50)
    return Color(
      red: 1.0,
      green: 1.0 - 0.50 * t,
      blue: 1.0 - 0.68 * t
    )
  }
}

