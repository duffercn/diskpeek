#!/usr/bin/env python3
"""
diskpeek GUI — macOS graphical interface for diskpeek.

Usage:
  python3 diskpeek_gui.py [path]      scan path (default: current directory)
  python3 diskpeek_gui.py -a [path]   include hidden files/directories
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

from PyQt6.QtCore import Qt, QTimer, QThread, pyqtSignal, QSortFilterProxyModel
from PyQt6.QtGui import QColor, QFont, QKeySequence, QShortcut, QAction
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QToolBar, QLineEdit, QLabel, QPushButton, QCheckBox, QTreeWidget,
    QTreeWidgetItem, QStatusBar, QFileDialog, QMessageBox, QMenu,
    QHeaderView, QSizePolicy,
)

from diskpeek import Scanner, human_size, quick_look, is_under, make_bar, make_pct, DOTDOT

POLL_MS   = 200   # UI refresh interval while scanning
COL_SIZE  = 0
COL_BAR   = 1
COL_PCT   = 2
COL_NAME  = 3

# Colours
C_DIR     = QColor("#0066CC")
C_DOTDOT  = QColor("#888888")
C_TAGGED_BG = QColor("#FFE0F8")
C_SIZE    = QColor("#20A0A0")
C_PCT     = QColor("#999999")
C_BAR     = QColor("#44AA44")


class DiskPeekWindow(QMainWindow):
    def __init__(self, root_path: Path, show_hidden: bool = False):
        super().__init__()
        self.root_path    = root_path
        self.current_dir  = root_path
        self._show_hidden = show_hidden
        self.mode         = "tree"      # "tree" | "flat"
        self.nav_stack: list  = []
        self.scanner_cache: dict = {}
        self.tagged_files: set = set()
        self.move_target: Path = Path.home() / "Downloads"
        self._scan_done_notified = True

        self._build_ui()
        self._start_scan()

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._poll)
        self._timer.start(POLL_MS)

    # ── UI construction ──────────────────────────────────────────────────────

    def _build_ui(self):
        self.setWindowTitle("diskpeek")
        self.resize(1150, 680)

        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        # ── Toolbar ──────────────────────────────────────────────────────────
        tb_widget = QWidget()
        tb_layout = QHBoxLayout(tb_widget)
        tb_layout.setContentsMargins(0, 0, 0, 0)
        tb_layout.setSpacing(6)

        btn_open = QPushButton("Open…")
        btn_open.setFixedWidth(60)
        btn_open.clicked.connect(self._choose_dir)
        tb_layout.addWidget(btn_open)

        tb_layout.addWidget(self._vsep())

        tb_layout.addWidget(QLabel("Path:"))
        self._path_edit = QLineEdit(str(self.current_dir))
        self._path_edit.setMinimumWidth(350)
        self._path_edit.returnPressed.connect(self._on_path_entry)
        tb_layout.addWidget(self._path_edit)

        tb_layout.addWidget(self._vsep())

        self._mode_btn = QPushButton("TREE")
        self._mode_btn.setFixedWidth(52)
        self._mode_btn.setCheckable(True)
        self._mode_btn.clicked.connect(self._toggle_mode)
        tb_layout.addWidget(self._mode_btn)

        tb_layout.addWidget(self._vsep())

        self._hidden_cb = QCheckBox("+hidden")
        self._hidden_cb.setChecked(self._show_hidden)
        self._hidden_cb.stateChanged.connect(self._toggle_hidden)
        tb_layout.addWidget(self._hidden_cb)

        tb_layout.addWidget(self._vsep())

        btn_rescan = QPushButton("Rescan")
        btn_rescan.setFixedWidth(60)
        btn_rescan.clicked.connect(self._rescan)
        tb_layout.addWidget(btn_rescan)

        tb_layout.addWidget(self._vsep())

        tb_layout.addWidget(QLabel("Filter:"))
        self._filter_edit = QLineEdit()
        self._filter_edit.setPlaceholderText("type to filter…")
        self._filter_edit.setFixedWidth(180)
        self._filter_edit.textChanged.connect(lambda _: self._refresh_list())
        tb_layout.addWidget(self._filter_edit)

        tb_layout.addWidget(self._vsep())

        btn_dest = QPushButton("Move dest…")
        btn_dest.clicked.connect(self._on_set_move_target)
        tb_layout.addWidget(btn_dest)

        tb_layout.addStretch()
        layout.addWidget(tb_widget)

        # ── File list ────────────────────────────────────────────────────────
        self.tree = QTreeWidget()
        self.tree.setColumnCount(4)
        self.tree.setHeaderLabels(["Size", "Usage", "%", "Name / Path"])
        self.tree.setRootIsDecorated(False)
        self.tree.setUniformRowHeights(True)
        self.tree.setAlternatingRowColors(True)
        self.tree.setSortingEnabled(False)
        self.tree.setSelectionMode(QTreeWidget.SelectionMode.SingleSelection)
        self.tree.setFont(QFont("Menlo", 12))

        hdr = self.tree.header()
        hdr.setSectionResizeMode(COL_SIZE, QHeaderView.ResizeMode.Fixed)
        hdr.setSectionResizeMode(COL_BAR,  QHeaderView.ResizeMode.Fixed)
        hdr.setSectionResizeMode(COL_PCT,  QHeaderView.ResizeMode.Fixed)
        hdr.setSectionResizeMode(COL_NAME, QHeaderView.ResizeMode.Stretch)
        self.tree.setColumnWidth(COL_SIZE, 95)
        self.tree.setColumnWidth(COL_BAR,  130)
        self.tree.setColumnWidth(COL_PCT,  45)

        self.tree.itemActivated.connect(self._on_activate)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._show_ctx)

        layout.addWidget(self.tree)

        # ── Status bar ───────────────────────────────────────────────────────
        self._status_bar = QStatusBar()
        self.setStatusBar(self._status_bar)
        self._scan_label = QLabel("")
        self._status_bar.addPermanentWidget(self._scan_label)

        # ── Keyboard shortcuts ───────────────────────────────────────────────
        def bind(key, fn):
            QShortcut(QKeySequence(key), self.tree, activated=fn)

        bind("Backspace",  self._go_back)
        bind("Left",       self._go_back)
        bind("d",          self._on_delete)
        bind("o",          self._on_open)
        bind("p",          self._on_preview)
        bind("c",          self._on_copy)
        bind("m",          self._on_move)
        bind("M",          self._on_set_move_target)
        bind("Space",      self._on_tag)
        bind("T",          self._on_clear_tags)
        bind("~",          self._goto_root)
        bind("r",          self._rescan)
        bind("Tab",        self._toggle_mode)
        bind("a",          self._toggle_hidden_key)

        # ── Context menu ─────────────────────────────────────────────────────
        self._ctx_menu = QMenu(self)
        self._ctx_menu.addAction("Open with Default App",  self._on_open)
        self._ctx_menu.addAction("Quick Look Preview",     self._on_preview)
        self._ctx_menu.addAction("Copy Path",              self._on_copy)
        self._ctx_menu.addSeparator()
        self._ctx_menu.addAction("Tag / Untag",            self._on_tag)
        self._ctx_menu.addAction("Move to Destination…",  self._on_move)
        self._ctx_menu.addSeparator()
        self._ctx_menu.addAction("Delete…",               self._on_delete)

        self.tree.setFocus()

    @staticmethod
    def _vsep():
        sep = QWidget()
        sep.setFixedWidth(1)
        sep.setStyleSheet("background: #ccc;")
        return sep

    # ── Scanning ─────────────────────────────────────────────────────────────

    def _get_scanner(self, d: Path) -> Scanner:
        if d in self.scanner_cache:
            return self.scanner_cache[d]

        best_ancestor = None
        for ancestor, sc in self.scanner_cache.items():
            if sc.done and is_under(d, ancestor):
                if best_ancestor is None or len(ancestor.parts) > len(best_ancestor.parts):
                    best_ancestor = ancestor

        if best_ancestor is not None:
            with self.scanner_cache[best_ancestor]._lock:
                derived = [(s, p) for s, p in self.scanner_cache[best_ancestor]._files
                           if is_under(p, d)]
            self.scanner_cache[d] = Scanner.from_files(d, derived, show_hidden=self._show_hidden)
        else:
            self.scanner_cache[d] = Scanner(d, show_hidden=self._show_hidden)

        return self.scanner_cache[d]

    def _start_scan(self):
        self.scanner = self._get_scanner(self.current_dir)
        self._path_edit.setText(str(self.current_dir))
        self._scan_done_notified = self.scanner.done
        self._refresh_list()

    def _poll(self):
        sc = self.scanner
        if sc.scanning:
            self._refresh_list()
            verb = "walking…" if sc.phase == "walking" else "sizing…"
            self._scan_label.setText(f"⟳ {verb}  {sc.found:,} files")
            self._scan_done_notified = False
        elif not self._scan_done_notified:
            self._scan_label.setText("")
            self._refresh_list()
            self._scan_done_notified = True

    # ── List management ───────────────────────────────────────────────────────

    def _current_items(self):
        f = self._filter_edit.text()
        if self.mode == "tree":
            items = self.scanner.tree_items(f)
            if self.current_dir != self.root_path:
                items = [(0, True, DOTDOT)] + items
            return items
        else:
            return self.scanner.flat_items(f)

    def _refresh_list(self):
        prev_path = self._selected_path()

        items = self._current_items()
        sc = self.scanner

        if self.mode == "tree":
            real = [(s, d, p) for s, d, p in items if p is not DOTDOT]
            max_size  = real[0][0] if real else 1
            vis_total = sum(s for s, _, _ in real) or 1
        else:
            max_size  = items[0][0] if items else 1
            vis_total = sum(s for s, _ in items) or 1

        self.tree.setUpdatesEnabled(False)
        self.tree.clear()

        restore_item = None

        for item in items:
            if self.mode == "tree":
                size, is_dir, path = item
            else:
                size, path = item
                is_dir = False

            is_dotdot = (path is DOTDOT)
            is_tagged = (not is_dotdot) and (not is_dir) and (path in self.tagged_files)

            size_str = "" if is_dotdot else f"{human_size(size):>8}"
            bar_str  = "" if is_dotdot else make_bar(size, max_size)
            pct_str  = "" if is_dotdot else make_pct(size, vis_total)

            if is_dotdot:
                name_str = "  .."
            elif self.mode == "tree":
                icon   = "▸ " if is_dir else "  "
                suffix = "/" if is_dir else ""
                name_str = f"  {icon}{path.name}{suffix}"
            else:
                try:
                    rel = str(path.relative_to(self.current_dir))
                except ValueError:
                    rel = str(path)
                ext = (path.suffix or "—").lower()
                name_str = f"  {ext:<9} {rel}"

            row = QTreeWidgetItem([size_str, bar_str, pct_str, name_str])
            row.setTextAlignment(COL_SIZE, Qt.AlignmentFlag.AlignRight  | Qt.AlignmentFlag.AlignVCenter)
            row.setTextAlignment(COL_PCT,  Qt.AlignmentFlag.AlignRight  | Qt.AlignmentFlag.AlignVCenter)
            row.setTextAlignment(COL_BAR,  Qt.AlignmentFlag.AlignLeft   | Qt.AlignmentFlag.AlignVCenter)
            row.setTextAlignment(COL_NAME, Qt.AlignmentFlag.AlignLeft   | Qt.AlignmentFlag.AlignVCenter)

            if is_dotdot:
                row.setForeground(COL_NAME, C_DOTDOT)
            elif is_dir:
                row.setForeground(COL_NAME, C_DIR)
            if not is_dotdot:
                row.setForeground(COL_SIZE, C_SIZE)
                row.setForeground(COL_BAR,  C_BAR)
                row.setForeground(COL_PCT,  C_PCT)
            if is_tagged:
                for col in range(4):
                    row.setBackground(col, C_TAGGED_BG)

            # Store metadata in the item
            row.setData(COL_NAME, Qt.ItemDataRole.UserRole, (size, path, is_dir))
            self.tree.addTopLevelItem(row)

            if path == prev_path:
                restore_item = row

        self.tree.setUpdatesEnabled(True)

        # Update title
        n = len(items)
        total = sc.total_size
        tagged_note = f"  |  {len(self.tagged_files)} tagged" if self.tagged_files else ""
        mode_tag = "TREE" if self.mode == "tree" else "FLAT"
        self.setWindowTitle(
            f"diskpeek [{mode_tag}]  {self.current_dir}"
            f"  [{n} items | {human_size(total)}]{tagged_note}"
        )
        self._mode_btn.setText(mode_tag)
        self._mode_btn.setChecked(self.mode == "flat")

        # Restore selection
        if restore_item:
            self.tree.setCurrentItem(restore_item)
            self.tree.scrollToItem(restore_item)
        elif self.tree.topLevelItemCount():
            self.tree.setCurrentItem(self.tree.topLevelItem(0))

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _selected_meta(self):
        item = self.tree.currentItem()
        if item is None:
            return None
        return item.data(COL_NAME, Qt.ItemDataRole.UserRole)

    def _selected_path(self) -> Path | None:
        meta = self._selected_meta()
        if meta is None:
            return None
        _, path, _ = meta
        return None if path is DOTDOT else path

    def _set_status(self, msg: str):
        self._status_bar.showMessage(msg, 8000)

    # ── Navigation ────────────────────────────────────────────────────────────

    def _choose_dir(self):
        d = QFileDialog.getExistingDirectory(self, "Open Directory", str(self.current_dir))
        if d:
            self.root_path = Path(d)
            self.current_dir = Path(d)
            self.nav_stack.clear()
            self.scanner_cache.clear()
            self.tagged_files.clear()
            self._filter_edit.clear()
            self._start_scan()
        self.tree.setFocus()

    def _on_path_entry(self):
        d = Path(os.path.expanduser(self._path_edit.text().strip()))
        if d.is_dir():
            self.root_path = d
            self.current_dir = d
            self.nav_stack.clear()
            self.scanner_cache.clear()
            self.tagged_files.clear()
            self._filter_edit.clear()
            self._start_scan()
        else:
            self._set_status(f"Not a directory: {d}")
        self.tree.setFocus()

    def _enter_dir(self, path: Path):
        self.nav_stack.append(self.current_dir)
        self.current_dir = path
        self._filter_edit.clear()
        self._start_scan()

    def _go_back(self):
        if self.current_dir == self.root_path:
            self._set_status("Already at root.")
            return
        self.current_dir = self.nav_stack.pop() if self.nav_stack else self.current_dir.parent
        self._filter_edit.clear()
        self._start_scan()

    def _goto_root(self):
        self.nav_stack.clear()
        self.current_dir = self.root_path
        self._filter_edit.clear()
        self._start_scan()

    def _on_activate(self, item, _col=None):
        meta = item.data(COL_NAME, Qt.ItemDataRole.UserRole)
        if meta is None:
            return
        size, path, is_dir = meta
        if path is DOTDOT:
            self._go_back()
        elif is_dir and self.mode == "tree":
            self._enter_dir(path)
        elif not is_dir:
            quick_look(path)

    # ── Mode / filter / hidden ────────────────────────────────────────────────

    def _toggle_mode(self):
        self.mode = "flat" if self.mode == "tree" else "tree"
        self._filter_edit.clear()
        self._refresh_list()

    def _toggle_hidden(self):
        self._show_hidden = self._hidden_cb.isChecked()
        self.scanner_cache.clear()
        self.tagged_files.clear()
        self._filter_edit.clear()
        self._start_scan()

    def _toggle_hidden_key(self):
        self._show_hidden = not self._show_hidden
        self._hidden_cb.setChecked(self._show_hidden)
        self.scanner_cache.clear()
        self.tagged_files.clear()
        self._filter_edit.clear()
        self._start_scan()

    def _rescan(self):
        stale = [d for d in self.scanner_cache
                 if d == self.current_dir
                 or is_under(self.current_dir, d)
                 or is_under(d, self.current_dir)]
        for d in stale:
            del self.scanner_cache[d]
        self._start_scan()

    # ── File actions ─────────────────────────────────────────────────────────

    def _on_open(self):
        path = self._selected_path()
        if path:
            subprocess.Popen(["open", str(path)],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self._set_status(f"Opened: {path}")

    def _on_preview(self):
        path = self._selected_path()
        if path is None:
            return
        meta = self._selected_meta()
        if meta and meta[2]:
            self._set_status("Preview is for files only. Double-click to enter a folder.")
        else:
            quick_look(path)

    def _on_copy(self):
        path = self._selected_path()
        if path:
            try:
                subprocess.run(["pbcopy"], input=str(path).encode(), check=True)
                self._set_status(f"Copied: {path}")
            except Exception as e:
                self._set_status(f"Copy failed: {e}")

    def _on_tag(self):
        meta = self._selected_meta()
        if meta is None:
            return
        size, path, is_dir = meta
        if path is DOTDOT or is_dir:
            self._set_status("Only files can be tagged.")
            return
        if path in self.tagged_files:
            self.tagged_files.discard(path)
        else:
            self.tagged_files.add(path)
        # Advance selection
        cur_idx = self.tree.indexOfTopLevelItem(self.tree.currentItem())
        if cur_idx >= 0 and cur_idx < self.tree.topLevelItemCount() - 1:
            next_item = self.tree.topLevelItem(cur_idx + 1)
            self.tree.setCurrentItem(next_item)
            self.tree.scrollToItem(next_item)
        self._refresh_list()

    def _on_clear_tags(self):
        self.tagged_files.clear()
        self._refresh_list()
        self._set_status("All tags cleared.")

    def _on_move(self):
        targets = sorted(self.tagged_files) if self.tagged_files else []
        if not targets:
            path = self._selected_path()
            if path is None:
                return
            meta = self._selected_meta()
            if meta and meta[2]:
                self._set_status("Can only move files, not folders.")
                return
            targets = [path]

        n = len(targets)
        msg = (f"Move {targets[0].name} → {self.move_target}/?"
               if n == 1 else
               f"Move {n} tagged files → {self.move_target}/?")
        if QMessageBox.question(self, "Confirm Move", msg) != QMessageBox.StandardButton.Yes:
            self._set_status("Cancelled.")
            return

        errors, done = [], []
        for p in targets:
            try:
                shutil.move(str(p), str(self.move_target))
                done.append(p)
            except Exception as e:
                errors.append(f"{p.name}: {e}")
        for p in done:
            self.tagged_files.discard(p)
            for sc in self.scanner_cache.values():
                sc.remove_file(p)
        self._refresh_list()
        if errors:
            self._set_status(f"Moved {len(done)}, errors: {'; '.join(errors)}")
        else:
            self._set_status(f"Moved {len(done)} file{'s' if len(done) != 1 else ''} → {self.move_target}/")

    def _on_set_move_target(self):
        d = QFileDialog.getExistingDirectory(self, "Set Move Destination", str(self.move_target))
        if d:
            self.move_target = Path(d)
            self._set_status(f"Move destination: {self.move_target}")
        self.tree.setFocus()

    def _on_delete(self):
        targets = sorted(self.tagged_files) if self.tagged_files else []
        if not targets:
            path = self._selected_path()
            if path is None:
                return
            meta = self._selected_meta()
            targets = [path]

        n = len(targets)
        msg = (f"Permanently delete '{targets[0].name}'?"
               if n == 1 else
               f"Permanently delete {n} tagged items?")
        btn = QMessageBox.warning(self, "Confirm Delete", msg,
                                  QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                  QMessageBox.StandardButton.No)
        if btn != QMessageBox.StandardButton.Yes:
            self._set_status("Cancelled.")
            return

        errors, done = [], []
        for p in targets:
            try:
                if p.is_dir():
                    shutil.rmtree(p)
                else:
                    p.unlink()
                done.append(p)
            except OSError as e:
                errors.append(f"{p.name}: {e}")
        for p in done:
            self.tagged_files.discard(p)
            for sc in self.scanner_cache.values():
                sc.remove_file(p)
        self._refresh_list()
        if errors:
            self._set_status(f"Deleted {len(done)}, errors: {'; '.join(errors)}")
        else:
            self._set_status(f"Deleted {len(done)} file{'s' if len(done) != 1 else ''}.")

    def _show_ctx(self, pos):
        item = self.tree.itemAt(pos)
        if item:
            self.tree.setCurrentItem(item)
        self._ctx_menu.exec(self.tree.viewport().mapToGlobal(pos))


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    args = sys.argv[1:]
    show_hidden = False
    if args and args[0] in ("-a", "--all"):
        show_hidden = True
        args = args[1:]
    root = Path(os.path.abspath(args[0] if args else "."))
    if not root.is_dir():
        print(f"Not a directory: {root}", file=sys.stderr)
        sys.exit(1)

    app = QApplication(sys.argv)
    app.setApplicationName("diskpeek")
    win = DiskPeekWindow(root, show_hidden)
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
