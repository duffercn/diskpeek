import Foundation
import Combine

// Stable sentinel URL for the ".." (go-up) row
let dotdotURL = URL(fileURLWithPath: "/__dotdot__")

struct FileItem: Identifiable, Equatable, Hashable {
    var id: String { url.path }
    let url: URL
    let size: Int64
    let isDir: Bool
    var isTagged: Bool = false
}

// Holds mutable state that lives on a background scan thread.
// Using a class (reference type) avoids Swift 6 "captured var" errors.
private final class ScanState {
    var buffer  = Data()
    var results: [(Int64, URL)] = []
}

final class ScannerModel: ObservableObject {

    // ── Published (always updated on main thread) ─────────────────────────────
    @Published var flatFiles:    [(Int64, URL)] = []
    @Published var isScanning    = false
    @Published var scannedCount  = 0
    @Published var phase         = ""
    @Published var totalSize:    Int64 = 0
    @Published var currentDir:   URL
    @Published var mode          = "tree"
    @Published var taggedURLs:   Set<URL> = []
    @Published var moveTarget    = FileManager.default
                                       .homeDirectoryForCurrentUser
                                       .appendingPathComponent("Downloads")
    @Published var statusMessage = ""

    var rootURL:    URL
    var showHidden: Bool
    var navStack:   [URL] = []

    private var scanProcess: Process?

    init(rootURL: URL, showHidden: Bool = false) {
        self.rootURL    = rootURL
        self.currentDir = rootURL
        self.showHidden = showHidden
    }

    // ── Derived views (instant, no I/O) ──────────────────────────────────────

    func treeItems(filter: String = "") -> [(Int64, Bool, URL)] {
        let prefix = currentDir.path.hasSuffix("/")
            ? currentDir.path : currentDir.path + "/"
        var childSizes: [String: (size: Int64, isDir: Bool)] = [:]

        for (size, fileURL) in flatFiles {
            let p = fileURL.path
            guard p.hasPrefix(prefix) else { continue }
            let rel = String(p.dropFirst(prefix.count))
            guard !rel.isEmpty else { continue }
            let firstSlash = rel.firstIndex(of: "/")
            let name   = firstSlash.map { String(rel[..<$0]) } ?? rel
            let isDir  = firstSlash != nil
            if var ex = childSizes[name] {
                ex.size += size; childSizes[name] = ex
            } else {
                childSizes[name] = (size, isDir)
            }
        }

        var result: [(Int64, Bool, URL)] = childSizes.compactMap { name, info in
            if !filter.isEmpty, !name.localizedCaseInsensitiveContains(filter) { return nil }
            return (info.size, info.isDir, currentDir.appendingPathComponent(name))
        }
        result.sort { $0.0 > $1.0 }
        return result
    }

    func flatItems(filter: String = "") -> [(Int64, URL)] {
        guard !filter.isEmpty else { return flatFiles }
        let low = filter.lowercased()
        return flatFiles.filter { $0.1.path.lowercased().contains(low) }
    }

    // ── Scanning ──────────────────────────────────────────────────────────────

    func scan(url: URL) {
        scanProcess?.terminate()
        scanProcess  = nil
        currentDir   = url
        isScanning   = true
        scannedCount = 0
        flatFiles    = []
        totalSize    = 0

        if let binary = findScannerBinary() {
            startGoScanner(binary: binary, url: url)
        } else {
            startSwiftScanner(url: url)
        }
    }

    func rescan() { scan(url: currentDir) }

    private func startGoScanner(binary: String, url: URL) {
        phase = "sizing"
        let proc  = Process()
        proc.executableURL = URL(fileURLWithPath: binary)
        proc.arguments = showHidden ? ["-a", url.path] : [url.path]

        let pipe = Pipe()
        proc.standardOutput = pipe
        proc.standardError  = Pipe()

        let state = ScanState()   // reference type — safe to share across closures

        pipe.fileHandleForReading.readabilityHandler = { [weak self] fh in
            state.buffer.append(fh.availableData)
            while let nl = state.buffer.firstIndex(of: UInt8(ascii: "\n")) {
                let line = state.buffer[..<nl]
                state.buffer.removeSubrange(...nl)
                guard let s   = String(data: line, encoding: .utf8), !s.isEmpty,
                      let tab = s.firstIndex(of: "\t"),
                      let sz  = Int64(s[..<tab]) else { continue }
                let path = String(s[s.index(after: tab)...])
                state.results.append((sz, URL(fileURLWithPath: path)))

                if state.results.count % 2000 == 0 {
                    let snap = state.results.sorted { $0.0 > $1.0 }
                    let tot  = snap.reduce(0) { $0 + $1.0 }
                    let cnt  = snap.count
                    DispatchQueue.main.async {
                        self?.flatFiles    = snap
                        self?.totalSize    = tot
                        self?.scannedCount = cnt
                    }
                }
            }
        }

        proc.terminationHandler = { [weak self] _ in
            pipe.fileHandleForReading.readabilityHandler = nil
            let sorted = state.results.sorted { $0.0 > $1.0 }
            let tot    = sorted.reduce(0) { $0 + $1.0 }
            DispatchQueue.main.async {
                self?.flatFiles    = sorted
                self?.totalSize    = tot
                self?.scannedCount = sorted.count
                self?.isScanning   = false
                self?.phase        = "done"
            }
        }

        do {
            try proc.run()
            scanProcess = proc
        } catch {
            startSwiftScanner(url: url)
        }
    }

    private func startSwiftScanner(url: URL) {
        phase = "walking"
        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            guard let self else { return }
            let fm   = FileManager.default
            let opts: FileManager.DirectoryEnumerationOptions = self.showHidden
                ? [.skipsPackageDescendants]
                : [.skipsHiddenFiles, .skipsPackageDescendants]
            guard let enumerator = fm.enumerator(
                at: url,
                includingPropertiesForKeys: [.fileSizeKey, .isRegularFileKey, .isSymbolicLinkKey],
                options: opts
            ) else { return }

            DispatchQueue.main.async { self.phase = "sizing" }
            var results: [(Int64, URL)] = []

            for case let fileURL as URL in enumerator {
                guard let res = try? fileURL.resourceValues(
                        forKeys: [.fileSizeKey, .isRegularFileKey, .isSymbolicLinkKey]),
                      res.isSymbolicLink != true,
                      res.isRegularFile  == true,
                      let sz = res.fileSize else { continue }
                results.append((Int64(sz), fileURL))
                if results.count % 500 == 0 {
                    let cnt = results.count
                    DispatchQueue.main.async { self.scannedCount = cnt }
                }
            }

            let sorted = results.sorted { $0.0 > $1.0 }
            let tot    = sorted.reduce(0) { $0 + $1.0 }
            DispatchQueue.main.async {
                self.flatFiles    = sorted
                self.totalSize    = tot
                self.scannedCount = sorted.count
                self.isScanning   = false
                self.phase        = "done"
            }
        }
    }

    // ── Navigation ────────────────────────────────────────────────────────────

    func enterDir(_ url: URL) {
        navStack.append(currentDir)
        currentDir = url
    }

    func goBack() {
        guard currentDir != rootURL else { statusMessage = "Already at root."; return }
        currentDir = navStack.isEmpty
            ? currentDir.deletingLastPathComponent()
            : navStack.removeLast()
    }

    func gotoRoot() { navStack.removeAll(); currentDir = rootURL }

    // ── Cache update ─────────────────────────────────────────────────────────

    func removeFromCache(_ url: URL) {
        flatFiles.removeAll { $0.1 == url }
        totalSize = flatFiles.reduce(0) { $0 + $1.0 }
    }
}
