# DVR-Scan GUI

A Qt desktop GUI for [DVR-Scan](https://www.dvr-scan.com/). Configure a motion
scan, run it, then review the results in an integrated video player: detected
motion events are drawn directly on the seek bar and listed in a table you can
click to jump to.

The scan runs in **scan-only** mode — DVR-Scan only *detects* motion and never
writes any extracted video clips to disk. The original video drives the player.

## Features

- **Configuration panel** for the common DVR-Scan options: threshold,
  background subtractor, kernel size, min event length, pre/post-event padding,
  scan range (start / end / duration), and performance (downscale, frame skip).
- **Integrated player** (Qt Multimedia) with play/pause, volume, and a live
  time readout.
- **Detection regions** — a small tool bar (Pointer / Rectangle / Polygon /
  Delete) lets you draw **one or more** regions directly on the video to limit
  motion detection (each is passed to DVR-Scan as its own `-a`). Regions are
  dashed outlines with no fill; coordinates map exactly to source pixels
  regardless of on-screen scaling. Edit corners with the pointer, remove a
  region with delete, or **disable** the whole set (kept on screen but excluded
  from the scan) via the **Enabled** checkbox.
- **Motion overlay seek bar** — every detected event is painted as a band on
  the timeline. Click or drag anywhere to scrub.
- **Results table** of events (index, start, duration). Single-click highlights
  the event on the timeline; double-click jumps there and plays.
- Scans run asynchronously with a live progress bar; the UI stays responsive.

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

1. **Browse…** to choose an input video (it loads into the player immediately).
2. Adjust detection options on the left if needed.
3. *(Optional)* Define one or more detection regions with the region tool bar:
   - **Pointer** (default): drag the corner handles to adjust an existing
     region; **Ctrl+click** a handle to delete that vertex (removing the last
     valid vertex deletes the region).
   - **Rectangle**: click two opposite corners (the box previews as you move).
   - **Polygon**: click each vertex (the next edge previews live); double-click,
     or click the first vertex, to close. Right-click removes the last vertex.
   - **Delete**: click a region to remove it.

   Finishing a shape (or pressing **Esc**) returns to the pointer. Uncheck
   **Enabled** to keep the regions but exclude them from the scan. Loading a new
   video clears them automatically.
4. Click **Scan for motion**. Progress shows in the bar below the button.
5. When the scan finishes, events appear on the seek bar and in the results
   table on the right.
6. **Double-click** any event to jump to it and start playback, or click once
   to highlight it on the timeline.

## Project layout

| File | Purpose |
|------|---------|
| `dvr_scan_gui/scanner.py` | Runs `dvr-scan` via `QProcess`, parses its timecode output, reports progress. |
| `dvr_scan_gui/video_view.py` | Graphics-view video display with the rectangular detection-region editor. |
| `dvr_scan_gui/timeline.py` | Custom seek-bar widget that overlays motion events. |
| `dvr_scan_gui/config_panel.py` | The DVR-Scan options form. |
| `dvr_scan_gui/main_window.py` | Main window: player, controls, results table, wiring. |
| `dvr_scan_gui/__main__.py` | Application entry point. |
