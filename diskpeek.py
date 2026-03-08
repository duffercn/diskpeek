#!/usr/bin/env python3
"""
diskpeek — Navigate, preview, and delete large files.

Two modes (toggle with TAB):
  TREE  browse directories like ncdu; folders show total recursive size
  FLAT  all files under current directory ranked by size (no tree drilling)

Keys:
  ↑ ↓ / j k        navigate list
  Enter / → / l     enter directory  (tree mode)
  Backspace / ← / h / - / u   go up one level
  ~                 jump back to root (starting directory)
  TAB               toggle tree ↔ flat mode
  SPACE             Quick Look preview (files only)
  c                 copy selected path to clipboard
  o                 open selected file/folder with default app (Finder for dirs)
  m                 move selected file to move destination (default: ~/Downloads)
  M                 change move destination folder
  d                 delete selected file (confirm with y)
  /                 filter by name  (ESC to clear, Enter to confirm)
  g / G             jump to top / bottom
  PgUp / PgDn       page up / down
  r                 rescan current directory
  q                 quit
"""

import curses
import os
import shutil
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path


# ── Helpers ────────────────────────────────────────────────────────────────────

def human_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} PB"


# Extensions where qlmanage is unreliable — open with the default app instead
_OPEN_EXTS = {
    ".mp4", ".mov", ".m4v", ".avi", ".mkv", ".webm", ".flv",
    ".mp3", ".m4a", ".aac", ".flac", ".wav", ".ogg", ".opus",
}

def quick_look(path: Path):
    """
    Preview a file. Uses qlmanage -p (floating Quick Look panel) for most
    files. Falls back to `open` (default app) for video/audio where qlmanage
    is unreliable on some macOS versions.
    """
    if path.suffix.lower() in _OPEN_EXTS:
        cmd = ["open", str(path)]
    else:
        cmd = ["qlmanage", "-p", str(path)]
    subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def is_under(child: Path, parent: Path) -> bool:
    try:
        child.relative_to(parent)
        return True
    except ValueError:
        return False


# Sentinel used as Path for the ".." (go-up) entry in tree mode
DOTDOT = None

# Bar layout constants
BAR_WIDTH = 10          # number of '#' chars inside brackets
MARK_COL  = 0           # 1-char tag marker (* or space)
SIZE_COL  = 2           # size column  (marker + space = 2 chars)
BAR_COL   = SIZE_COL + 10 + 1         # size(10) + space(1)      → col 13
PCT_COL   = BAR_COL + BAR_WIDTH + 3   # "[" + bar + "]" + space  → col 26
NAME_COL  = PCT_COL + 5               # "NNN%" + "  "            → col 31


def make_bar(size: int, max_size: int) -> str:
    """Return e.g. '[#####     ]' — fill proportional to max_size."""
    filled = round(size / max_size * BAR_WIDTH) if max_size > 0 else 0
    filled = max(0, min(BAR_WIDTH, filled))
    return f"[{'#' * filled}{' ' * (BAR_WIDTH - filled)}]"


def make_pct(size: int, total: int) -> str:
    """Return e.g. ' 42%' — percentage of total."""
    if total <= 0:
        return "  0%"
    return f"{size / total * 100:3.0f}%"


# ── Background scanner ─────────────────────────────────────────────────────────

class Scanner:
    """
    Recursively scans a directory in a background thread.
    Once done, provides both flat and tree views of the data.
    """

    # Number of worker threads for concurrent stat() calls.
    # stat() is I/O-bound so more threads = faster on SSDs.
    _STAT_WORKERS = min(32, (os.cpu_count() or 4) * 4)

    def __init__(self, root: Path):
        self.root = root
        self._files: list = []          # [(size, Path)] sorted largest-first
        self._tree_cache: list = []     # cached tree_items() result
        self._total_size: int = 0       # sum of all file sizes (for title bar)
        self._lock = threading.Lock()
        self.done = False
        self.error: str = ""
        self._found: int = 0            # live count for progress display
        self._phase: str = "walking"    # "walking" | "sizing" | "done"
        threading.Thread(target=self._run, daemon=True).start()

    def _build_tree_cache(self, files: list) -> list:
        """Compute tree_items result from a file list. Called in background thread."""
        children: dict = {}
        for size, path in files:
            try:
                rel = path.relative_to(self.root)
            except ValueError:
                continue
            parts = rel.parts
            key = parts[0]
            child_path = self.root / key
            is_dir = len(parts) > 1
            if key in children:
                children[key][1] += size
            else:
                children[key] = [is_dir, size, child_path]
        result = [(info[1], info[0], info[2]) for info in children.values()]
        result.sort(reverse=True)
        return result

    def _publish(self, files: list):
        """Update _files + derived caches under the lock."""
        tree = self._build_tree_cache(files)
        total = sum(s for s, _ in files)
        with self._lock:
            self._files = files
            self._tree_cache = tree
            self._total_size = total
            self._found = len(files)

    def _walk(self) -> list:
        """
        Fast directory traversal using os.scandir() with an explicit stack.
        is_file() / is_dir() are free — they use the cached d_type from the
        kernel's directory listing, so no extra syscall per entry.
        Returns a list of plain path strings for all non-symlink files.
        """
        file_paths = []
        stack = [str(self.root)]
        while stack:
            dirpath = stack.pop()
            try:
                with os.scandir(dirpath) as it:
                    for entry in it:
                        try:
                            if entry.is_symlink():
                                continue
                            if entry.is_file(follow_symlinks=False):
                                file_paths.append(entry.path)
                            elif entry.is_dir(follow_symlinks=False):
                                stack.append(entry.path)
                        except OSError:
                            pass
            except OSError:
                pass
            # Update count periodically so the UI spinner shows progress
            if len(file_paths) % 500 == 0:
                with self._lock:
                    self._found = len(file_paths)
        return file_paths

    @staticmethod
    def _stat_one(path_str: str):
        """Return (size, Path) or None on error. Called from thread pool."""
        try:
            return (os.stat(path_str, follow_symlinks=False).st_size, Path(path_str))
        except OSError:
            return None

    def _run(self):
        results = []
        try:
            # ── Phase 1: walk (fast — no stat() per entry) ─────────────────
            with self._lock:
                self._phase = "walking"
            all_paths = self._walk()
            with self._lock:
                self._found = len(all_paths)
                self._phase = "sizing"

            # ── Phase 2: stat concurrently ──────────────────────────────────
            FLUSH_EVERY = 2000
            with ThreadPoolExecutor(max_workers=self._STAT_WORKERS) as pool:
                for i, item in enumerate(
                    pool.map(self._stat_one, all_paths, chunksize=256)
                ):
                    if item is not None:
                        results.append(item)
                    if (i + 1) % FLUSH_EVERY == 0:
                        self._publish(sorted(results, reverse=True))

        except Exception as e:
            self.error = str(e)

        self._publish(sorted(results, reverse=True))
        with self._lock:
            self._phase = "done"
            self.done = True

    @classmethod
    def from_files(cls, root: Path, files: list) -> "Scanner":
        """
        Create a Scanner pre-populated from a parent's file list.
        _files is available immediately (flat mode works at once).
        _tree_cache is built in a background thread so the UI isn't blocked.
        """
        inst = object.__new__(cls)
        inst.root = root
        inst._lock = threading.Lock()
        inst._files = files          # sorted, ready immediately
        inst._tree_cache = []
        inst._total_size = 0
        inst._found = len(files)
        inst._phase = "sizing"       # show brief spinner while tree is built
        inst.done = False
        inst.error = ""
        threading.Thread(target=inst._finish_from_files, daemon=True).start()
        return inst

    def _finish_from_files(self):
        """Background: build tree cache from already-loaded _files."""
        tree  = self._build_tree_cache(self._files)
        total = sum(s for s, _ in self._files)
        with self._lock:
            self._tree_cache = tree
            self._total_size = total
            self._phase = "done"
            self.done = True

    @property
    def scanning(self) -> bool:
        return not self.done

    @property
    def found(self) -> int:
        """Number of files discovered so far (updated incrementally)."""
        with self._lock:
            return self._found

    @property
    def phase(self) -> str:
        with self._lock:
            return self._phase

    # ── Views ──────────────────────────────────────────────────────────────────

    def flat_items(self, filter_str: str = "") -> list:
        """All files sorted by size. No filter: O(1) cache hit. With filter: O(n)."""
        with self._lock:
            files = self._files          # reference, not copy
        if not filter_str:
            return files
        low = filter_str.lower()
        return [(s, p) for s, p in files if low in str(p).lower()]

    def tree_items(self, filter_str: str = "") -> list:
        """Immediate children with sizes. No filter: O(1) cache hit. With filter: O(m)."""
        with self._lock:
            result = self._tree_cache    # reference, not copy
        if not filter_str:
            return result
        low = filter_str.lower()
        return [(s, d, p) for s, d, p in result if low in p.name.lower()]

    @property
    def total_size(self) -> int:
        with self._lock:
            return self._total_size

    def remove_file(self, path: Path):
        """
        Remove one file from the cached data without any I/O or rescan.
        Updates _files, _total_size, and _tree_cache surgically.
        """
        with self._lock:
            # Pull out the file and capture its size in one pass
            file_size = 0
            new_files = []
            for s, p in self._files:
                if p == path:
                    file_size = s
                else:
                    new_files.append((s, p))
            self._files = new_files
            self._found = len(new_files)
            self._total_size = max(0, self._total_size - file_size)

            # Surgically update tree cache — O(m), m = # immediate children
            try:
                child_name = path.relative_to(self.root).parts[0]
                new_tree = []
                for s, is_dir, p in self._tree_cache:
                    if p.name == child_name:
                        new_s = s - file_size
                        if new_s > 0:
                            new_tree.append((new_s, is_dir, p))
                        # else: last file in this entry gone — drop it
                    else:
                        new_tree.append((s, is_dir, p))
                new_tree.sort(reverse=True)
                self._tree_cache = new_tree
            except (ValueError, IndexError):
                pass  # path not under this root, nothing to update


# ── UI drawing ─────────────────────────────────────────────────────────────────

C_SEL        = 1   # cursor row
C_SIZE       = 2   # size column
C_TITLE      = 3   # title bar — idle
C_EXT        = 4   # file extension
C_DIM        = 5   # help / muted text
C_WARN       = 6   # errors / status / confirm
C_DIR        = 7   # directory name
C_BAR        = 8   # [####    ] bar
C_PCT        = 9   # percentage number
C_TITLE_BUSY = 10  # title bar — scanning
C_TAGGED     = 11  # tagged/multi-selected file marker


_SPINNER = r"|/-\\"

def draw(stdscr, *, root, current_dir, mode, items, selected, offset,
         filter_str, filter_mode, status, scanning, found, phase, total_size,
         move_target, move_target_mode, move_target_input, tagged_files):
    h, w = stdscr.getmaxyx()
    HEADER, FOOTER = 3, 2
    list_h = max(1, h - HEADER - FOOTER)

    stdscr.erase()

    # ── Title ──────────────────────────────────────────────────────────────────
    mode_tag = "[FLAT]" if mode == "flat" else "[TREE]"
    if scanning:
        spin = _SPINNER[int(time.time() * 6) % len(_SPINNER)]
        verb = "walking…" if phase == "walking" else "sizing…"
        scan_tag = f"  {spin} {verb} {found:,} files"
    else:
        scan_tag = ""
    tag_tag = f"  {len(tagged_files)} tagged" if tagged_files else ""
    title = f" diskpeek {mode_tag}  {current_dir}  [{len(items)} items | {human_size(total_size)}]{tag_tag}{scan_tag}"
    try:
        title_color = curses.color_pair(C_TITLE_BUSY if scanning else C_TITLE)
        stdscr.addstr(0, 0, title[:w - 1], title_color | curses.A_BOLD)
    except curses.error:
        pass

    # ── Filter / move-target bar (row 1) ───────────────────────────────────────
    if move_target_mode:
        bar = f" Move to: {move_target_input}_"
        bar_color = curses.color_pair(C_WARN) | curses.A_BOLD
    else:
        bar = f" Filter: {filter_str}{'_' if filter_mode else ''}   [move→ {move_target}/]"
        bar_color = curses.color_pair(C_DIM)
    try:
        stdscr.addstr(1, 0, bar[:w - 1], bar_color)
    except curses.error:
        pass

    # ── Column headers ─────────────────────────────────────────────────────────
    bar_hdr = f"{'':>{BAR_WIDTH + 2}}"
    if mode == "tree":
        col_hdr = f"  {'SIZE':>10}  {bar_hdr}  PCT%  NAME"
    else:
        col_hdr = f"  {'SIZE':>10}  {bar_hdr}  PCT%  {'EXT':<8}  PATH"
    try:
        stdscr.addstr(2, 0, col_hdr[:w - 1], curses.A_UNDERLINE)
    except curses.error:
        pass

    # ── Rows ───────────────────────────────────────────────────────────────────
    # Pre-compute bar scaling values — exclude the ".." sentinel (size 0)
    real_items = [it for it in items if it[2] is not DOTDOT] if mode == "tree" else items
    max_size   = real_items[0][0] if real_items else 1
    vis_total  = sum(it[0] for it in real_items) or 1

    for i in range(list_h):
        idx = offset + i
        if idx >= len(items):
            break
        row    = HEADER + i
        is_sel = idx == selected
        item   = items[idx]
        size   = item[0]

        # Resolve path and tagged status
        if mode == "tree":
            _, is_dir, path = item
            is_dotdot = path is DOTDOT
            is_tagged = (not is_dotdot) and (not is_dir) and path in tagged_files
        else:
            _, path = item
            is_dotdot = False
            is_dir    = False
            is_tagged = path in tagged_files

        size_str = f"{human_size(size):>10}"
        bar_str  = make_bar(size, max_size)
        pct_str  = make_pct(size, vis_total)
        marker   = "*" if is_tagged else " "

        try:
            if is_sel:
                # Full cyan highlight row
                stdscr.addstr(row, 0, " " * (w - 1), curses.color_pair(C_SEL))
            elif is_tagged:
                # Subtle magenta background for tagged rows
                stdscr.addstr(row, 0, " " * (w - 1), curses.color_pair(C_TAGGED))

            # Marker at col 0
            mark_color = curses.color_pair(C_SEL if is_sel else C_TAGGED) | curses.A_BOLD
            stdscr.addstr(row, MARK_COL, marker, mark_color)

            if mode == "tree" and is_dotdot:
                stdscr.addstr(row, NAME_COL, f"  .."[:w - NAME_COL - 1],
                              curses.color_pair(C_SEL if is_sel else C_DIR) | curses.A_BOLD)
            else:
                seg_color = curses.color_pair(C_SEL) if is_sel else 0

                # Size
                stdscr.addstr(row, SIZE_COL, size_str,
                              curses.color_pair(C_SEL if is_sel else C_SIZE))
                # Bar
                stdscr.addstr(row, BAR_COL, bar_str,
                              curses.color_pair(C_SEL if is_sel else C_BAR))
                # Pct
                stdscr.addstr(row, PCT_COL, pct_str,
                              curses.color_pair(C_SEL if is_sel else C_PCT))

                if mode == "tree":
                    name = path.name + ("/" if is_dir else "")
                    name_color = curses.color_pair(C_SEL if is_sel else (C_DIR if is_dir else 0))
                    stdscr.addstr(row, NAME_COL, f"  {name}"[:w - NAME_COL - 1], name_color)
                else:
                    try:
                        rel = str(path.relative_to(current_dir))
                    except ValueError:
                        rel = str(path)
                    ext = (path.suffix or "").lower()
                    stdscr.addstr(row, NAME_COL, f"  {ext:<8}",
                                  curses.color_pair(C_SEL if is_sel else C_EXT))
                    stdscr.addstr(row, NAME_COL + 10, f"  {rel}"[:w - NAME_COL - 11],
                                  curses.color_pair(C_SEL if is_sel else 0))
        except curses.error:
            pass

    # ── Status ─────────────────────────────────────────────────────────────────
    if status:
        try:
            stdscr.addstr(h - 2, 0, f" {status}"[:w - 1], curses.color_pair(C_WARN) | curses.A_BOLD)
        except curses.error:
            pass

    # ── Help bar ───────────────────────────────────────────────────────────────
    if mode == "tree":
        help_str = " ↑↓/jk nav  Enter/l enter  h/-/BS up  ~ root  TAB flat  SPACE tag  T clr  p preview  m move  M dest  c copy  o open  d del  / filter  q quit"
    else:
        help_str = " ↑↓/jk nav  h/-/BS up  ~ root  TAB tree  SPACE tag  T clr  p preview  m move  M dest  c copy  o open  d del  / filter  g/G  r rescan  q quit"
    try:
        stdscr.addstr(h - 1, 0, help_str[:w - 1], curses.color_pair(C_DIM))
    except curses.error:
        pass

    stdscr.refresh()


# ── Scroll helper ──────────────────────────────────────────────────────────────

def clamp_scroll(selected: int, offset: int, list_h: int) -> int:
    if selected < offset:
        return selected
    if selected >= offset + list_h:
        return selected - list_h + 1
    return offset


# ── Main loop ──────────────────────────────────────────────────────────────────

def main(stdscr, root: Path):
    curses.curs_set(0)
    curses.use_default_colors()
    curses.init_pair(C_SEL,   curses.COLOR_BLACK,  curses.COLOR_CYAN)
    curses.init_pair(C_SIZE,  curses.COLOR_CYAN,   -1)
    curses.init_pair(C_TITLE,      curses.COLOR_WHITE, curses.COLOR_BLUE)
    curses.init_pair(C_TITLE_BUSY, curses.COLOR_BLACK, curses.COLOR_YELLOW)
    curses.init_pair(C_EXT,   curses.COLOR_YELLOW, -1)
    curses.init_pair(C_DIM,   curses.COLOR_WHITE,  -1)
    curses.init_pair(C_WARN,  curses.COLOR_RED,    -1)
    curses.init_pair(C_DIR,   curses.COLOR_GREEN,  -1)
    curses.init_pair(C_BAR,    curses.COLOR_GREEN,   -1)
    curses.init_pair(C_PCT,    curses.COLOR_WHITE,   -1)
    curses.init_pair(C_TAGGED, curses.COLOR_BLACK,   curses.COLOR_MAGENTA)

    stdscr.timeout(150)   # non-blocking getch; refresh while scanning

    # ── State ──────────────────────────────────────────────────────────────────
    current_dir: Path = root
    mode: str = "tree"            # "tree" | "flat"

    # Navigation history for back: stack of (dir, selected, offset)
    nav_stack: list = []

    # Scanner cache — avoid re-scanning the same directory
    scanner_cache: dict = {}

    def get_scanner(d: Path) -> Scanner:
        if d in scanner_cache:
            return scanner_cache[d]

        # Look for the closest completed ancestor scan we can derive from.
        # A filtered subsequence of a sorted list is already sorted — free!
        best_ancestor = None
        for ancestor, sc in scanner_cache.items():
            if sc.done and is_under(d, ancestor):
                if best_ancestor is None or len(ancestor.parts) > len(best_ancestor.parts):
                    best_ancestor = ancestor

        if best_ancestor is not None:
            with scanner_cache[best_ancestor]._lock:
                derived = [(s, p) for s, p in scanner_cache[best_ancestor]._files
                           if is_under(p, d)]
            scanner_cache[d] = Scanner.from_files(d, derived)
        else:
            scanner_cache[d] = Scanner(d)

        return scanner_cache[d]

    scanner = get_scanner(current_dir)

    selected = 0
    offset = 0
    filter_str = ""
    filter_mode = False
    status = ""
    move_target = Path.home() / "Downloads"
    move_target_input = ""
    move_target_mode = False   # True while user is typing a new move destination
    tagged_files: set = set()  # paths tagged for batch operations

    while True:
        h, w = stdscr.getmaxyx()
        HEADER, FOOTER = 3, 2
        list_h = max(1, h - HEADER - FOOTER)

        if mode == "tree":
            items = scanner.tree_items(filter_str)
            # Prepend ".." entry when not at the starting root
            if current_dir != root:
                items = [(0, True, DOTDOT)] + items
        else:
            items = scanner.flat_items(filter_str)

        # Clamp selection after list changes (e.g. post-delete, post-scan)
        if items:
            selected = min(selected, len(items) - 1)
        else:
            selected = 0
        offset = clamp_scroll(selected, offset, list_h)

        draw(
            stdscr,
            root=root,
            current_dir=current_dir,
            mode=mode,
            items=items,
            selected=selected,
            offset=offset,
            filter_str=filter_str,
            filter_mode=filter_mode,
            status=status,
            scanning=scanner.scanning,
            found=scanner.found,
            phase=scanner.phase,
            total_size=scanner.total_size,
            move_target=move_target,
            move_target_mode=move_target_mode,
            move_target_input=move_target_input,
            tagged_files=tagged_files,
        )

        key = stdscr.getch()
        if key == -1:
            continue   # timeout → redraw (animates scanning indicator)

        status = ""

        # Resolve the currently selected path (used by c / o / SPACE / d)
        def selected_path():
            if not items:
                return None
            item = items[selected]
            if mode == "tree":
                _, is_dir, path = item
                return current_dir.parent if path is DOTDOT else path
            else:
                _, path = item
                return path

        # ── Filter input ───────────────────────────────────────────────────────
        if filter_mode:
            if key == 27:                            # ESC — clear & exit filter
                filter_str = ""
                filter_mode = False
                selected = 0
                offset = 0
            elif key in (curses.KEY_BACKSPACE, 127, 8):
                filter_str = filter_str[:-1]
                selected = 0
                offset = 0
            elif key == ord("\n"):
                filter_mode = False
            elif 32 <= key <= 126:
                filter_str += chr(key)
                selected = 0
                offset = 0
            continue

        # ── Move-target input mode (M key) ─────────────────────────────────────
        if move_target_mode:
            if key == 27:                                   # ESC — cancel
                move_target_mode = False
                move_target_input = ""
            elif key in (curses.KEY_BACKSPACE, 127, 8):
                move_target_input = move_target_input[:-1]
            elif key == ord("\n"):
                candidate = Path(os.path.expanduser(move_target_input.strip()))
                if candidate.is_dir():
                    move_target = candidate
                    status = f"Move destination set to: {move_target}"
                else:
                    status = f"Not a directory: {candidate}"
                move_target_mode = False
                move_target_input = ""
            elif 32 <= key <= 126:
                move_target_input += chr(key)
            continue

        # ── Normal keys ────────────────────────────────────────────────────────

        if key == ord("q"):
            break

        # Toggle mode
        elif key == ord("\t"):
            mode = "flat" if mode == "tree" else "tree"
            filter_str = ""
            selected = 0
            offset = 0

        # Filter
        elif key == ord("/"):
            filter_mode = True

        # Rescan — purge current dir, all ancestors (they contain our data),
        # and all descendants (derived from our data), then do a fresh scan.
        elif key == ord("r"):
            stale = [d for d in scanner_cache
                     if d == current_dir
                     or is_under(current_dir, d)   # d is an ancestor
                     or is_under(d, current_dir)]  # d is a descendant
            for d in stale:
                del scanner_cache[d]
            scanner = get_scanner(current_dir)
            selected = 0
            offset = 0

        # Navigation — up
        elif key in (curses.KEY_UP, ord("k")):
            if selected > 0:
                selected -= 1
                offset = clamp_scroll(selected, offset, list_h)

        # Navigation — down
        elif key in (curses.KEY_DOWN, ord("j")):
            if selected < len(items) - 1:
                selected += 1
                offset = clamp_scroll(selected, offset, list_h)

        # Jump top / bottom
        elif key == ord("g"):
            selected = 0
            offset = 0

        elif key == ord("G"):
            selected = max(0, len(items) - 1)
            offset = clamp_scroll(selected, offset, list_h)

        # Page up / down
        elif key == curses.KEY_PPAGE:
            selected = max(0, selected - list_h)
            offset = max(0, offset - list_h)

        elif key == curses.KEY_NPAGE:
            selected = min(max(0, len(items) - 1), selected + list_h)
            offset = clamp_scroll(selected, offset, list_h)

        # Enter directory (tree mode) or no-op (flat mode)
        elif key in (curses.KEY_ENTER, ord("\n"), curses.KEY_RIGHT, ord("l")):
            if not items or mode == "flat":
                continue
            _, is_dir, path = items[selected]
            if not is_dir:
                continue
            if path is DOTDOT:
                # ".." selected — same as going back
                if nav_stack:
                    current_dir, selected, offset = nav_stack.pop()
                else:
                    current_dir = current_dir.parent
                    selected = 0
                    offset = 0
                scanner = get_scanner(current_dir)
                filter_str = ""
            else:
                nav_stack.append((current_dir, selected, offset))
                current_dir = path
                scanner = get_scanner(current_dir)
                selected = 0
                offset = 0
                filter_str = ""

        # Go up / back  (many aliases so any terminal config works)
        # Hard stop at root — never navigate above the starting directory
        elif key in (curses.KEY_BACKSPACE, 127, 8, curses.KEY_LEFT,
                     ord("h"), ord("-"), ord("u")):
            if current_dir == root:
                status = "Already at root. Use ~ to stay here."
            elif nav_stack:
                current_dir, selected, offset = nav_stack.pop()
                scanner = get_scanner(current_dir)
                filter_str = ""
            else:
                current_dir = current_dir.parent
                selected = 0
                offset = 0
                scanner = get_scanner(current_dir)
                filter_str = ""

        # Jump to root
        elif key == ord("~"):
            nav_stack.clear()
            current_dir = root
            scanner = get_scanner(current_dir)
            selected = 0
            offset = 0
            filter_str = ""

        # Move file(s) to move_target — batch if tagged, else current
        elif key == ord("m"):
            targets = sorted(tagged_files) if tagged_files else []
            if not targets:
                cur = selected_path()
                if cur is None or cur is DOTDOT or cur.is_dir():
                    status = "Can only move files, not folders."
                    continue
                targets = [cur]

            n = len(targets)
            h2, w2 = stdscr.getmaxyx()
            if n == 1:
                prompt = f" Move {targets[0].name} → {move_target}/? [y/N]: "
            else:
                prompt = f" Move {n} tagged files → {move_target}/? [y/N]: "
            try:
                stdscr.addstr(h2 - 2, 0, " " * (w2 - 1), curses.color_pair(C_WARN))
                stdscr.addstr(h2 - 2, 0, prompt[:w2 - 1], curses.color_pair(C_WARN) | curses.A_BOLD)
            except curses.error:
                pass
            stdscr.refresh()
            stdscr.timeout(-1)
            confirm = stdscr.getch()
            stdscr.timeout(150)
            if confirm == ord("y"):
                errors, done = [], []
                for path in targets:
                    try:
                        shutil.move(str(path), str(move_target))
                        done.append(path)
                    except Exception as e:
                        errors.append(f"{path.name}: {e}")
                for path in done:
                    tagged_files.discard(path)
                    for sc in scanner_cache.values():
                        sc.remove_file(path)
                if errors:
                    status = f"Moved {len(done)}, errors: {'; '.join(errors)}"
                else:
                    status = f"Moved {len(done)} file{'s' if len(done) != 1 else ''} → {move_target}/"
            else:
                status = "Cancelled."

        # Set move destination folder
        elif key == ord("M"):
            move_target_mode = True
            move_target_input = str(move_target)

        # Copy path to clipboard
        elif key == ord("c"):
            path = selected_path()
            if path:
                try:
                    subprocess.run(["pbcopy"], input=str(path).encode(), check=True)
                    status = f"Copied: {path}"
                except Exception as e:
                    status = f"Copy failed: {e}"

        # Open with default app (Finder for folders)
        elif key == ord("o"):
            path = selected_path()
            if path:
                subprocess.Popen(["open", str(path)],
                                 stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL)
                status = f"Opened: {path}"

        # Tag / untag current item for batch operations
        elif key == ord(" "):
            path = selected_path()
            if path and path is not DOTDOT and not path.is_dir():
                if path in tagged_files:
                    tagged_files.discard(path)
                else:
                    tagged_files.add(path)
                # Auto-advance cursor after tagging
                if selected < len(items) - 1:
                    selected += 1
                    offset = clamp_scroll(selected, offset, list_h)
            elif path and path.is_dir():
                status = "Only files can be tagged. Enter folder to tag files inside."

        # Clear all tags
        elif key == ord("T"):
            tagged_files.clear()
            status = "All tags cleared."

        # Preview with p (was Space)
        elif key == ord("p"):
            path = selected_path()
            if path and path is not DOTDOT:
                if path.is_dir():
                    status = "Press Enter/l to enter folder. p previews files only."
                else:
                    quick_look(path)

        # Delete — batch if tagged, else current
        elif key == ord("d"):
            targets = sorted(tagged_files) if tagged_files else []
            if not targets:
                # fall back to current item
                cur = selected_path()
                if cur is None or cur is DOTDOT or cur.is_dir():
                    status = "Cannot delete a folder here. Enter it first, or switch to flat mode."
                    continue
                targets = [cur]

            n = len(targets)
            h2, w2 = stdscr.getmaxyx()
            if n == 1:
                prompt = f" Delete {targets[0].name}? [y/N]: "
            else:
                prompt = f" Delete {n} tagged files? [y/N]: "
            try:
                stdscr.addstr(h2 - 2, 0, " " * (w2 - 1), curses.color_pair(C_WARN))
                stdscr.addstr(h2 - 2, 0, prompt[:w2 - 1], curses.color_pair(C_WARN) | curses.A_BOLD)
            except curses.error:
                pass
            stdscr.refresh()
            stdscr.timeout(-1)
            confirm = stdscr.getch()
            stdscr.timeout(150)

            if confirm == ord("y"):
                errors, done = [], []
                for path in targets:
                    try:
                        path.unlink()
                        done.append(path)
                    except OSError as e:
                        errors.append(f"{path.name}: {e}")
                for path in done:
                    tagged_files.discard(path)
                    for sc in scanner_cache.values():
                        sc.remove_file(path)
                if errors:
                    status = f"Deleted {len(done)}, errors: {'; '.join(errors)}"
                else:
                    status = f"Deleted {len(done)} file{'s' if len(done) != 1 else ''}."
            else:
                status = "Cancelled."


# ── Entry point ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    root = Path(os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else "."))
    if not root.is_dir():
        print(f"Not a directory: {root}", file=sys.stderr)
        sys.exit(1)
    try:
        curses.wrapper(main, root)
    except KeyboardInterrupt:
        pass
