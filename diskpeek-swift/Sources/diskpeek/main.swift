import AppKit
import SwiftUI

// ── Parse command-line arguments ──────────────────────────────────────────────

var showHidden = false
var rootPath: String? = nil

for arg in CommandLine.arguments.dropFirst() {
    if arg == "-a" || arg == "--all" { showHidden = true }
    else { rootPath = arg }
}

let rootURL: URL = {
    if let p = rootPath {
        return URL(fileURLWithPath: p).standardized
    }
    return URL(fileURLWithPath: FileManager.default.currentDirectoryPath)
}()

var isDir: ObjCBool = false
guard FileManager.default.fileExists(atPath: rootURL.path, isDirectory: &isDir), isDir.boolValue else {
    fputs("Not a directory: \(rootURL.path)\n", stderr)
    exit(1)
}

// ── App setup ─────────────────────────────────────────────────────────────────

class AppDelegate: NSObject, NSApplicationDelegate {
    var window: NSWindow!

    func applicationDidFinishLaunching(_ notification: Notification) {
        let model   = ScannerModel(rootURL: rootURL, showHidden: showHidden)
        let content = ContentView().environmentObject(model)

        window = NSWindow(
            contentRect: NSRect(x: 0, y: 0, width: 1150, height: 680),
            styleMask:   [.titled, .closable, .miniaturizable, .resizable, .fullSizeContentView],
            backing:     .buffered,
            defer:       false
        )
        window.title = "diskpeek"
        window.center()
        window.setFrameAutosaveName("diskpeek.main")
        window.contentView = NSHostingView(rootView: content)
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }
}

let app      = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.setActivationPolicy(.regular)
app.run()
