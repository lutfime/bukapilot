import Foundation

/// Discrete first-order low-pass filter.
///
/// Direct equivalent of `openpilot.common.filter_simple.FirstOrderFilter` used
/// by the device UI for the confidence ball and torque bar. Smoothing is what
/// makes both indicators move gracefully instead of jittering frame-to-frame.
///
/// `y[n] = (1 - alpha) * y[n-1] + alpha * x[n]`
/// where `alpha = dt / (tc + dt)` and `tc` is the cutoff time constant.
struct FirstOrderFilter {
  private(set) var value: Double
  private let alpha: Double

  /// - Parameters:
  ///   - initial: starting value.
  ///   - cutoff: time constant in seconds (larger = smoother/laggier).
  ///   - dt: sample period in seconds (the rate we expect `update` to be called).
  init(initial: Double, cutoff: Double, dt: Double) {
    self.value = initial
    self.alpha = dt / (cutoff + dt)
  }

  mutating func update(_ input: Double) {
    value = (1 - alpha) * value + alpha * input
  }
}
