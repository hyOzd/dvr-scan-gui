# DVR-Scan GUI

A Qt desktop GUI for [DVR-Scan](https://www.dvr-scan.com/). Configure a motion
scan, run it, then review the results in an integrated video player: detected
motion events are drawn directly on the seek bar and listed in a table you can
click to jump to.

The scan runs in **scan-only** mode — DVR-Scan only *detects* motion and never
writes any extracted video clips to disk. The original video drives the player.

## Features

- **Multi-file queue** — add many videos (via **Add…**, the file menu, or by
  **dragging them onto the list**; duplicates are rejected with a warning). The
  list shows each file's scan status (*Not scanned / Queued / Scanning / Done /
  Needs update / Failed*), a slim progress bar, and its detection count. Select
  any file to load it in the player and review its events — even while other
  files are still scanning.
- **Scan one or scan all** — **Scan selected** scans the current file; **Scan
  all** scans every file in order, skipping any already scanned with the current
  settings. A file is flagged **Needs update** whenever the settings or its
  region change after it was scanned.
- **Parallel scanning** — choose how many files to scan at once (defaults to
  about half your CPU cores). The main progress bar shows **total** progress
  across the batch with an **ETA**.
- **Tabbed left panel** — the file list and the **Configuration** form share a
  tabbed panel on the left. Starting a scan automatically switches back to the
  **Files** tab so you can watch progress.
- **Configuration tab** for the common DVR-Scan options: threshold, background
  subtractor, kernel size, min event length, pre/post-event padding, scan range
  (start / end / duration), and performance (downscale, frame skip).
- **Integrated player** (Qt Multimedia) with play/pause, volume, and a live
  time readout.
- **Per-file detection regions** — a small tool bar (Pointer / Rectangle /
  Polygon / Delete) lets you draw **one or more** regions directly on the video
  to limit motion detection (each is passed to DVR-Scan as its own `-a`).
  Regions are remembered per file. They are dashed outlines with no fill;
  coordinates map exactly to source pixels regardless of on-screen scaling. Edit
  corners with the pointer, remove a region with delete, or **disable** the set
  (kept on screen but excluded from the scan) via the **Enabled** checkbox.
- **Motion overlay seek bar** — every detected event is painted as a band on
  the timeline. Click or drag anywhere to scrub.
- **Results table** of events (index, start, duration). Single-click highlights
  the event on the timeline; double-click jumps there and plays.
- Scans run asynchronously; the UI stays responsive and a running batch can be
  **cancelled** at any time (already-completed results are kept).

## Requirements

- Python 3.12
- [`dvr-scan`](https://pypi.org/project/dvr-scan/) on your `PATH`
  (`pip install dvr-scan`)
- Playback uses Qt Multimedia. PySide6 6.11 ships a bundled FFmpeg backend, so
  no extra system media packages are normally required.

## Setup

Dependencies are managed with **pipenv**:

```bash
pipenv install
```

## Run

```bash
pipenv run gui
# or
pipenv run python -m dvr_scan_gui
```

## Usage

1. **Add…** one or more videos, or drag video files onto the file list. Click a
   file to load it into the player.
2. Adjust detection options on the **Configuration** tab (next to **Files** in
   the left panel) if needed.
3. *(Optional)* Define one or more detection regions with the region tool bar.
   Regions are saved per file:
   - **Pointer** (default): drag the corner handles to adjust an existing
     region; **Ctrl+click** a handle to delete that vertex (removing the last
     valid vertex deletes the region).
   - **Rectangle**: click two opposite corners (the box previews as you move).
   - **Polygon**: click each vertex (the next edge previews live); double-click,
     or click the first vertex, to close. Right-click removes the last vertex.
   - **Delete**: click a region to remove it.

   Finishing a shape (or pressing **Esc**) returns to the pointer. Uncheck
   **Enabled** to keep a file's regions but exclude them from the scan.
4. Set **Parallel scans** to how many files to process at once, then click
   **Scan selected** (current file) or **Scan all** (every file, skipping ones
   already up to date). The total progress bar and ETA track the batch; each
   file's row shows its own status and progress.
5. As each file finishes, its events appear on the seek bar and results table.
   You can select and play any finished file while others are still scanning.
6. **Double-click** any event to jump to it and start playback, or click once
   to highlight it on the timeline. The **⏮ / ⏭** buttons beside Play step to
   the previous / next detected event; stepping past the last event jumps to the
   next file (and stepping before the first event jumps to the previous file).
7. Click **Cancel** to stop a running batch; files already finished keep their
   results. Changing a setting or a file's region marks scanned files as
   **Needs update** so **Scan all** will refresh them.

## Project layout

| File | Purpose |
|------|---------|
| `dvr_scan_gui/scanner.py` | Runs `dvr-scan` via `QProcess` (one `ScanWorker` per file), a `ScanManager` that queues files and runs several at once, and the timecode parser. |
| `dvr_scan_gui/file_list.py` | The multi-file model (`FileEntry`), the per-file row widget, and the drag-and-drop file list. |
| `dvr_scan_gui/video_view.py` | Graphics-view video display with the multi-region detection-region editor. |
| `dvr_scan_gui/timeline.py` | Custom seek-bar widget that overlays motion events. |
| `dvr_scan_gui/config_panel.py` | The DVR-Scan options form. |
| `dvr_scan_gui/main_window.py` | Main window: file list, player, controls, results table, scan orchestration, wiring. |
| `dvr_scan_gui/__main__.py` | Application entry point. |
