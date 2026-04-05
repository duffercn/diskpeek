import SwiftUI
import AppKit

struct ContentView: View {
    @EnvironmentObject var model: ScannerModel
    @State private var selectedID: String? = nil
    @State private var filterText  = ""
    @State private var pathText    = ""

    // ── Derived list ─────────────────────────────────────────────────────────

    var displayItems: [FileItem] {
        var items: [FileItem] = []
        if model.mode == "tree" {
            if model.currentDir != model.rootURL {
                items.append(FileItem(url: dotdotURL, size: 0, isDir: true))
            }
            for (size, isDir, url) in model.treeItems(filter: filterText) {
                items.append(FileItem(url: url, size: size, isDir: isDir,
                                      isTagged: model.taggedURLs.contains(url)))
            }
        } else {
            for (size, url) in model.flatItems(filter: filterText) {
                items.append(FileItem(url: url, size: size, isDir: false,
                                      isTagged: model.taggedURLs.contains(url)))
            }
        }
        return items
    }

    var realItems: [FileItem] { displayItems.filter { $0.url != dotdotURL } }
    var maxSize:   Int64 { realItems.first?.size ?? 1 }
    var visTotal:  Int64 { max(1, realItems.reduce(0) { $0 + $1.size }) }

    // ── Body ─────────────────────────────────────────────────────────────────

    var body: some View {
        VStack(spacing: 0) {
            toolbar
            Divider()
            fileTable
            Divider()
            statusBar
        }
        .frame(minWidth: 800, minHeight: 400)
        .onAppear {
            pathText = model.currentDir.path
            model.scan(url: model.currentDir)
        }
        .onChange(of: model.currentDir) { _, val in pathText = val.path }
        // Hidden keyboard shortcut buttons
        .background(shortcutButtons)
    }

    // ── Toolbar ──────────────────────────────────────────────────────────────

    var toolbar: some View {
        HStack(spacing: 8) {
            Button("Open…", action: chooseDir)

            separator

            Text("Path:").foregroundStyle(.secondary)
            TextField("", text: $pathText)
                .textFieldStyle(.roundedBorder)
                .frame(minWidth: 250)
                .onSubmit(navigateToPath)

            separator

            Button(model.mode == "tree" ? "TREE" : "FLAT", action: toggleMode)
                .buttonStyle(.bordered)

            Toggle("+hidden", isOn: Binding(
                get: { model.showHidden },
                set: { val in
                    model.showHidden = val
                    model.taggedURLs.removeAll()
                    filterText = ""
                    model.scan(url: model.currentDir)
                }
            ))

            separator

            Button("Rescan", action: model.rescan)

            separator

            Text("Filter:").foregroundStyle(.secondary)
            TextField("", text: $filterText)
                .textFieldStyle(.roundedBorder)
                .frame(width: 160)

            separator

            Button("Move dest…", action: chooseMoveTarget)

            Spacer()
        }
        .padding(.horizontal, 8)
        .padding(.vertical, 6)
    }

    var separator: some View {
        Divider().frame(height: 20)
    }

    // ── File table ────────────────────────────────────────────────────────────

    var fileTable: some View {
        Table(displayItems, selection: $selectedID) {
            TableColumn("Size") { item in
                Group {
                    if item.url != dotdotURL {
                        Text(humanSize(item.size))
                            .foregroundStyle(Color(nsColor: .systemTeal))
                    }
                }
                .frame(maxWidth: .infinity, alignment: .trailing)
            }
            .width(90)

            TableColumn("Usage") { item in
                Group {
                    if item.url != dotdotURL {
                        Text(makeBar(item.size, maxSize: maxSize))
                            .foregroundStyle(.green)
                    }
                }
                .font(.system(.body, design: .monospaced))
            }
            .width(130)

            TableColumn("%") { item in
                Group {
                    if item.url != dotdotURL {
                        Text(makePct(item.size, total: visTotal))
                            .foregroundStyle(.secondary)
                    }
                }
                .frame(maxWidth: .infinity, alignment: .trailing)
            }
            .width(45)

            TableColumn("Name / Path") { item in
                nameCell(for: item)
            }
        }
        .contextMenu(forSelectionType: String.self,
                     menu: { _ in contextMenuItems() },
                     primaryAction: { ids in
                         if let id = ids.first,
                            let item = displayItems.first(where: { $0.id == id }) {
                             activate(item: item)
                         }
                     })
    }

    @ViewBuilder
    func nameCell(for item: FileItem) -> some View {
        if item.url == dotdotURL {
            Text("  ..").foregroundStyle(.secondary)
        } else if model.mode == "tree" {
            HStack(spacing: 3) {
                if item.isDir {
                    Text("▸").foregroundStyle(.blue)
                    Text(item.url.lastPathComponent + "/").foregroundStyle(.blue)
                } else {
                    Text("  " + item.url.lastPathComponent)
                }
                if item.isTagged { Spacer(); Text("●").foregroundStyle(.purple).font(.caption) }
            }
        } else {
            let ext = item.url.pathExtension.lowercased()
            let rel: String = {
                let base = model.currentDir.path.hasSuffix("/")
                    ? model.currentDir.path : model.currentDir.path + "/"
                return item.url.path.hasPrefix(base)
                    ? String(item.url.path.dropFirst(base.count))
                    : item.url.path
            }()
            HStack(spacing: 0) {
                Text("  " + (ext.isEmpty ? "—" : ext).padding(toLength: 10, withPad: " ", startingAt: 0))
                    .foregroundStyle(.orange)
                    .font(.system(.body, design: .monospaced))
                Text(rel)
                if item.isTagged { Spacer(); Text("●").foregroundStyle(.purple).font(.caption) }
            }
        }
    }

    @ViewBuilder
    func contextMenuItems() -> some View {
        Button("Open with Default App",  action: onOpen)
        Button("Quick Look Preview",     action: onPreview)
        Button("Copy Path",              action: onCopy)
        Divider()
        Button("Tag / Untag",            action: onTag)
        Button("Move to Destination…",   action: onMove)
        Divider()
        Button("Delete…", role: .destructive, action: onDelete)
    }

    // ── Status bar ────────────────────────────────────────────────────────────

    var statusBar: some View {
        HStack {
            Text(model.statusMessage.isEmpty ? " " : model.statusMessage)
                .foregroundStyle(.secondary)
                .lineLimit(1)
            Spacer()
            if model.isScanning {
                ProgressView().scaleEffect(0.6).progressViewStyle(.circular)
                Text(model.phase == "walking" ? "Walking…" : "Sizing…")
                    .foregroundStyle(.secondary).font(.caption)
                Text("\(model.scannedCount) files")
                    .foregroundStyle(.secondary).font(.caption)
            } else {
                Text("\(realItems.count) items  |  \(humanSize(model.totalSize))")
                    .foregroundStyle(.secondary).font(.caption)
                if !model.taggedURLs.isEmpty {
                    Text("· \(model.taggedURLs.count) tagged")
                        .foregroundStyle(.purple).font(.caption)
                }
            }
        }
        .padding(.horizontal, 8)
        .padding(.vertical, 4)
    }

    // ── Hidden keyboard shortcut buttons ─────────────────────────────────────

    var shortcutButtons: some View {
        Group {
            Button("") { model.goBack()  }.keyboardShortcut(.delete,    modifiers: [])
            Button("") { model.goBack()  }.keyboardShortcut(.leftArrow, modifiers: [])
            Button("") { model.gotoRoot() }.keyboardShortcut("`",        modifiers: [])
            Button("") { toggleMode()    }.keyboardShortcut("\t",        modifiers: [])
            Button("") { onOpen()        }.keyboardShortcut("o",         modifiers: [])
            Button("") { onPreview()     }.keyboardShortcut("p",         modifiers: [])
            Button("") { onCopy()        }.keyboardShortcut("c",         modifiers: [])
            Button("") { onTag()         }.keyboardShortcut(" ",         modifiers: [])
            Button("") { onClearTags()   }.keyboardShortcut("t",         modifiers: [.shift])
            Button("") { onMove()        }.keyboardShortcut("m",         modifiers: [])
            Button("") { chooseMoveTarget() }.keyboardShortcut("m",      modifiers: [.shift])
            Button("") { onDelete()      }.keyboardShortcut("d",         modifiers: [])
            Button("") { model.rescan()  }.keyboardShortcut("r",         modifiers: [])
        }
        .opacity(0)
        .allowsHitTesting(false)
        .frame(width: 0, height: 0)
    }

    // ── Helpers ───────────────────────────────────────────────────────────────

    func selectedItem() -> FileItem? {
        displayItems.first { $0.id == selectedID }
    }

    func activate(item: FileItem) {
        if item.url == dotdotURL          { model.goBack() }
        else if item.isDir && model.mode == "tree" { model.enterDir(item.url) }
        else if !item.isDir               { quickLook(url: item.url) }
    }

    // ── Navigation actions ────────────────────────────────────────────────────

    func chooseDir() {
        let panel = NSOpenPanel()
        panel.canChooseDirectories = true
        panel.canChooseFiles = false
        panel.directoryURL = model.currentDir
        guard panel.runModal() == .OK, let url = panel.url else { return }
        model.rootURL = url
        model.navStack.removeAll()
        model.taggedURLs.removeAll()
        filterText = ""
        model.scan(url: url)
    }

    func navigateToPath() {
        let url = URL(fileURLWithPath: pathText).standardized
        var isDir: ObjCBool = false
        guard FileManager.default.fileExists(atPath: url.path, isDirectory: &isDir),
              isDir.boolValue else {
            model.statusMessage = "Not a directory: \(pathText)"
            return
        }
        model.rootURL = url
        model.navStack.removeAll()
        model.taggedURLs.removeAll()
        filterText = ""
        model.scan(url: url)
    }

    func toggleMode() {
        model.mode = model.mode == "tree" ? "flat" : "tree"
        filterText = ""
    }

    func chooseMoveTarget() {
        let panel = NSOpenPanel()
        panel.canChooseDirectories = true
        panel.canChooseFiles = false
        panel.directoryURL = model.moveTarget
        panel.message = "Choose move destination folder"
        guard panel.runModal() == .OK, let url = panel.url else { return }
        model.moveTarget = url
        model.statusMessage = "Move destination: \(url.path)"
    }

    // ── File actions ──────────────────────────────────────────────────────────

    func onOpen() {
        guard let item = selectedItem(), item.url != dotdotURL else { return }
        NSWorkspace.shared.open(item.url)
        model.statusMessage = "Opened: \(item.url.path)"
    }

    func onPreview() {
        guard let item = selectedItem(), item.url != dotdotURL else { return }
        if item.isDir { model.statusMessage = "Preview is for files only." }
        else { quickLook(url: item.url) }
    }

    func onCopy() {
        guard let item = selectedItem(), item.url != dotdotURL else { return }
        NSPasteboard.general.clearContents()
        NSPasteboard.general.setString(item.url.path, forType: .string)
        model.statusMessage = "Copied: \(item.url.path)"
    }

    func onTag() {
        guard let item = selectedItem(), item.url != dotdotURL, !item.isDir else {
            model.statusMessage = "Only files can be tagged."
            return
        }
        if model.taggedURLs.contains(item.url) { model.taggedURLs.remove(item.url) }
        else { model.taggedURLs.insert(item.url) }

        // Advance selection to next row
        if let idx = displayItems.firstIndex(where: { $0.id == selectedID }),
           idx + 1 < displayItems.count {
            selectedID = displayItems[idx + 1].id
        }
    }

    func onClearTags() {
        model.taggedURLs.removeAll()
        model.statusMessage = "All tags cleared."
    }

    func onMove() {
        var targets = Array(model.taggedURLs).sorted { $0.path < $1.path }
        if targets.isEmpty {
            guard let item = selectedItem(), item.url != dotdotURL, !item.isDir else {
                model.statusMessage = "Can only move files, not folders."
                return
            }
            targets = [item.url]
        }
        let label = targets.count == 1
            ? targets[0].lastPathComponent
            : "\(targets.count) tagged files"
        let alert = NSAlert()
        alert.messageText     = "Move Files"
        alert.informativeText = "Move \(label) → \(model.moveTarget.path)/?"
        alert.addButton(withTitle: "Move")
        alert.addButton(withTitle: "Cancel")
        guard alert.runModal() == .alertFirstButtonReturn else {
            model.statusMessage = "Cancelled."
            return
        }
        var done: [URL] = [], errors: [String] = []
        for url in targets {
            let dest = model.moveTarget.appendingPathComponent(url.lastPathComponent)
            do { try FileManager.default.moveItem(at: url, to: dest); done.append(url) }
            catch { errors.append("\(url.lastPathComponent): \(error.localizedDescription)") }
        }
        done.forEach { model.taggedURLs.remove($0); model.removeFromCache($0) }
        model.statusMessage = errors.isEmpty
            ? "Moved \(done.count) file\(done.count == 1 ? "" : "s") → \(model.moveTarget.path)/"
            : "Moved \(done.count), errors: \(errors.joined(separator: "; "))"
    }

    func onDelete() {
        var targets = Array(model.taggedURLs).sorted { $0.path < $1.path }
        if targets.isEmpty {
            guard let item = selectedItem(), item.url != dotdotURL else {
                return
            }
            targets = [item.url]
        }
        let label = targets.count == 1
            ? "'\(targets[0].lastPathComponent)'"
            : "\(targets.count) tagged files"
        let alert = NSAlert()
        alert.messageText     = "Confirm Delete"
        alert.informativeText = "Permanently delete \(label)?"
        alert.alertStyle      = .warning
        alert.addButton(withTitle: "Delete")
        alert.addButton(withTitle: "Cancel")
        guard alert.runModal() == .alertFirstButtonReturn else {
            model.statusMessage = "Cancelled."
            return
        }
        var done: [URL] = [], errors: [String] = []
        for url in targets {
            do { try FileManager.default.removeItem(at: url); done.append(url) }
            catch { errors.append("\(url.lastPathComponent): \(error.localizedDescription)") }
        }
        done.forEach { model.taggedURLs.remove($0); model.removeFromCache($0) }
        model.statusMessage = errors.isEmpty
            ? "Deleted \(done.count) file\(done.count == 1 ? "" : "s")."
            : "Deleted \(done.count), errors: \(errors.joined(separator: "; "))"
    }
}
