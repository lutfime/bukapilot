import SwiftUI

/// Compact stat display: small label above a monospaced value.
struct StatBox: View {
  let title: String
  let value: String

  var body: some View {
    VStack(alignment: .leading, spacing: 1) {
      Text(title).font(.system(size: 9)).foregroundStyle(.tertiary)
      Text(value).font(.system(size: 14, weight: .medium, design: .monospaced))
    }
    .padding(8)
    .background(Color.secondary.opacity(0.08), in: RoundedRectangle(cornerRadius: 6))
  }
}
