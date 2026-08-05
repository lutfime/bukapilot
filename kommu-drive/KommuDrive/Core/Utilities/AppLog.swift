import Foundation
import os

/// Lightweight unified logger. Uses OSLog so it lands in Console.app and Xcode console.
enum AppLog {
  static let subsystem = "ai.kommu.KommuDrive"
  private static let log = Logger(subsystem: subsystem, category: "app")

  static func info(_ message: String)  { log.info("ℹ️ \(message, privacy: .public)") }
  static func warn(_ message: String)  { log.warning("⚠️ \(message, privacy: .public)") }
  static func error(_ message: String) { log.error("⛔️ \(message, privacy: .public)") }
  static func debug(_ message: String) { log.debug("🐛 \(message, privacy: .public)") }
}
