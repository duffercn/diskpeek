import Foundation
import AppKit

// ── Size formatting ───────────────────────────────────────────────────────────

func humanSize(_ n: Int64) -> String {
    var d = Double(n)
    for unit in ["B", "KB", "MB", "GB", "TB"] {
        if abs(d) < 1024 { return String(format: "%.1f %@", d, unit) }
        d /= 1024
    }
    return String(format: "%.1f PB", d)
}

private let barWidth = 10

func makeBar(_ size: Int64, maxSize: Int64) -> String {
    let ratio = maxSize > 0 ? Double(size) / Double(maxSize) : 0
    let filled = max(0, min(barWidth, Int(ratio * Double(barWidth))))
    return "[" + String(repeating: "#", count: filled)
               + String(repeating: " ", count: barWidth - filled) + "]"
}

func makePct(_ size: Int64, total: Int64) -> String {
    guard total > 0 else { return "  0%" }
    return String(format: "%3.0f%%", Double(size) / Double(total) * 100)
}

// ── Quick Look ────────────────────────────────────────────────────────────────

func quickLook(url: URL) {
    let videoAudio: Set<String> = ["mp4","mov","m4v","avi","mkv","webm","flv",
                                   "mp3","m4a","aac","flac","wav","ogg","opus"]
    if videoAudio.contains(url.pathExtension.lowercased()) {
        NSWorkspace.shared.open(url)
    } else {
        let task = Process()
        task.executableURL = URL(fileURLWithPath: "/usr/bin/qlmanage")
        task.arguments = ["-p", url.path]
        task.standardOutput = FileHandle.nullDevice
        task.standardError  = FileHandle.nullDevice
        try? task.run()
    }
}

// ── Find the Go scanner binary ────────────────────────────────────────────────

func findScannerBinary() -> String? {
    // 1. Next to the running executable (inside .app bundle or build output)
    let exe = URL(fileURLWithPath: CommandLine.arguments[0])
    let candidates = [
        exe.deletingLastPathComponent().appendingPathComponent("diskpeek-scanner").path,
        exe.deletingLastPathComponent().appendingPathComponent("../Frameworks/diskpeek-scanner").path,
    ]
    for p in candidates where FileManager.default.isExecutableFile(atPath: p) { return p }

    // 2. System PATH via `which`
    let task = Process()
    task.executableURL = URL(fileURLWithPath: "/usr/bin/which")
    task.arguments = ["diskpeek-scanner"]
    let pipe = Pipe()
    task.standardOutput = pipe
    task.standardError  = Pipe()
    try? task.run(); task.waitUntilExit()
    let out = String(data: pipe.fileHandleForReading.readDataToEndOfFile(), encoding: .utf8)?
                  .trimmingCharacters(in: .whitespacesAndNewlines)
    return out?.isEmpty == false ? out : nil
}
