# diskpeek

A fast terminal file explorer for macOS — find large files, preview them, and clean up disk space without leaving your terminal.

![diskpeek screenshot placeholder](https://via.placeholder.com/800x400?text=diskpeek+TUI)

## Features

- **Tree mode** — browse directories like ncdu, with total recursive sizes per folder
- **Flat mode** — all files under current directory ranked by size (no drilling needed)
- **`TAB`** to switch between modes instantly
- **Size bar + percentage** for every item, ncdu-style
- **Quick Look preview** with `p` — works for PDFs, images, and videos (MP4 etc.)
- **Multi-select** with `Space` — tag files and batch delete or move in one go
- **Move to folder** — send files to a configurable destination (default: `~/Downloads`)
- **Copy path** to clipboard with `c`
- **Fast scanning** — concurrent `stat()` calls + incremental results while scanning
- **Smart cache** — entering a subdirectory reuses the parent scan instantly

---

## Installation

### Option A — Direct download (recommended)

```bash
curl -L https://github.com/duffercn/diskpeek/releases/latest/download/diskpeek \
  -o /usr/local/bin/diskpeek && chmod +x /usr/local/bin/diskpeek
```

### Option B — Run from source (requires Python 3.9+, no extra packages)

```bash
curl -O https://raw.githubusercontent.com/duffercn/diskpeek/main/diskpeek.py
python3 diskpeek.py
```

---

## Usage

```bash
diskpeek                  # scan current directory
diskpeek ~/Downloads      # scan a specific directory
diskpeek /               # scan entire disk (takes a moment)
```

---

## Key Bindings

### Navigation

| Key | Action |
|-----|--------|
| `↑` / `↓` or `j` / `k` | Move cursor up / down |
| `Enter` / `l` / `→` | Enter directory (tree mode) |
| `h` / `-` / `Backspace` / `←` | Go up to parent directory |
| `~` | Jump back to starting directory |
| `g` / `G` | Jump to top / bottom of list |
| `PgUp` / `PgDn` | Page up / down |
| `TAB` | Toggle between **tree** and **flat** mode |

### File Actions

| Key | Action |
|-----|--------|
| `p` | Quick Look preview (PDF, image, MP4, etc.) |
| `Space` | Tag / untag file for batch operation (cursor advances) |
| `T` | Clear all tags |
| `d` | Delete tagged files (or current file if none tagged) |
| `m` | Move tagged files to move destination (or current file) |
| `M` | Set move destination folder (default: `~/Downloads`) |
| `c` | Copy full path to clipboard |
| `o` | Open file/folder with default app (Finder for directories) |

### Other

| Key | Action |
|-----|--------|
| `/` | Filter by name (substring match) — `ESC` to clear |
| `r` | Rescan current directory (clears cache) |
| `q` | Quit |

---

## Workflow Example — Cleaning Up Disk Space

1. `diskpeek ~/` — start at home
2. Navigate tree to find a big folder, press `TAB` to see all files ranked by size
3. Press `Space` on files you want to delete to tag them (`*` marker appears)
4. Press `p` on any file to Quick Look preview before deciding
5. Press `d` — confirm once to delete all tagged files
6. Press `r` to rescan if you made changes outside diskpeek

---

## Requirements

- macOS 10.15 Catalina or later
- No Python required when using the pre-built binary

---

## License

MIT
