"""The application's main window."""

from __future__ import annotations

import os
import time

from PySide6.QtCore import QSize, QStandardPaths, Qt, QUrl
from PySide6.QtGui import QAction, QKeySequence, QPalette
from PySide6.QtMultimedia import QAudioOutput, QMediaMetaData, QMediaPlayer
from PySide6.QtWidgets import (
    QAbstractItemView,
    QButtonGroup,
    QCheckBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSlider,
    QSpinBox,
    QSplitter,
    QStyle,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .config_panel import ConfigPanel
from .file_list import (
    PATH_ROLE,
    STATUS_DONE,
    STATUS_FAILED,
    STATUS_IDLE,
    STATUS_QUEUED,
    STATUS_RUNNING,
    FileEntry,
    FileItemWidget,
    FileListWidget,
)
from .icons import tool_icon
from .scanner import MotionEvent, ScanManager, ms_to_timecode
from .timeline import TimelineSeekBar
from .video_view import (
    TOOL_DELETE,
    TOOL_POINTER,
    TOOL_POLYGON,
    TOOL_RECTANGLE,
    VideoView,
)

_VIDEO_FILTER = (
    "Video files (*.mp4 *.avi *.mkv *.mov *.m4v *.mpg *.mpeg *.wmv *.flv *.webm);;"
    "All files (*)"
)


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("DVR-Scan GUI")
        self.resize(1320, 760)

        # Per-file state.
        self._entries: dict[str, FileEntry] = {}
        self._items: dict[str, QListWidgetItem] = {}
        self._current_path: str | None = None
        self._events: list[MotionEvent] = []  # events of the selected file

        # Batch-scan bookkeeping.
        self._batch: set[str] = set()
        self._batch_start = 0.0
        self._scanning_active = False

        self._scanner = ScanManager(self)
        self._scanner.started.connect(self._on_scan_started)
        self._scanner.progress.connect(self._on_scan_progress)
        self._scanner.log.connect(self._on_scan_log)
        self._scanner.finished.connect(self._on_scan_finished)
        self._scanner.failed.connect(self._on_scan_failed)
        self._scanner.cancelled.connect(self._on_scan_cancelled)
        self._scanner.idle.connect(self._on_manager_idle)

        self._build_ui()
        self._build_player()

        self._scanner.set_max_concurrent(self.cores_spin.value())
        self._update_scan_buttons()

        if self._scanner.executable() is None:
            self._set_status(
                "dvr-scan not found on PATH — install it with 'pip install dvr-scan'."
            )

    # ---- UI construction --------------------------------------------------

    def _build_ui(self) -> None:
        self._build_actions()

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self._build_files_side())
        self._config_container = self._build_config_side()
        splitter.addWidget(self._config_container)
        splitter.addWidget(self._build_player_side())
        splitter.addWidget(self._build_results_side())
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 0)
        splitter.setStretchFactor(2, 1)
        splitter.setStretchFactor(3, 0)
        splitter.setSizes([250, 280, 560, 250])
        self.setCentralWidget(splitter)

        # Wire the config toggle now that the container exists.
        self.config_action.toggled.connect(self._config_container.setVisible)

    def _build_actions(self) -> None:
        self.add_action = QAction("&Add files…", self)
        self.add_action.setShortcut(QKeySequence.StandardKey.Open)
        self.add_action.triggered.connect(self._browse_add)

        self.remove_action = QAction("&Remove selected", self)
        self.remove_action.triggered.connect(self._remove_selected)
        self.remove_action.setEnabled(False)

        self.config_action = QAction("&Configuration", self)
        self.config_action.setCheckable(True)
        self.config_action.setChecked(True)
        self.config_action.setToolTip("Show or hide the detection settings panel.")

        toolbar = self.addToolBar("Main")
        toolbar.setMovable(False)
        toolbar.addAction(self.add_action)
        toolbar.addAction(self.remove_action)
        toolbar.addSeparator()
        toolbar.addAction(self.config_action)

        file_menu = self.menuBar().addMenu("&File")
        file_menu.addAction(self.add_action)
        file_menu.addAction(self.remove_action)
        view_menu = self.menuBar().addMenu("&View")
        view_menu.addAction(self.config_action)

    def _build_files_side(self) -> QWidget:
        container = QWidget()
        layout = QVBoxLayout(container)

        layout.addWidget(QLabel("<b>Files</b>"))

        button_row = QHBoxLayout()
        self.add_button = QPushButton("Add…")
        self.add_button.clicked.connect(self._browse_add)
        self.remove_button = QPushButton("Remove")
        self.remove_button.setEnabled(False)
        self.remove_button.clicked.connect(self._remove_selected)
        button_row.addWidget(self.add_button)
        button_row.addWidget(self.remove_button)
        button_row.addStretch(1)
        layout.addLayout(button_row)

        self.file_list = FileListWidget()
        self.file_list.filesDropped.connect(self._add_files)
        self.file_list.currentItemChanged.connect(self._on_current_changed)
        layout.addWidget(self.file_list, 1)

        hint = QLabel("Drag video files here to add them.")
        hint.setStyleSheet("color: gray; font-size: 11px;")
        layout.addWidget(hint)

        layout.addWidget(self._hline())

        self.scan_selected_button = QPushButton("Scan selected")
        self.scan_selected_button.setEnabled(False)
        self.scan_selected_button.clicked.connect(self._scan_selected)
        layout.addWidget(self.scan_selected_button)

        self.scan_all_button = QPushButton("Scan all")
        self.scan_all_button.setDefault(True)
        self.scan_all_button.setEnabled(False)
        self.scan_all_button.clicked.connect(self._scan_all)
        layout.addWidget(self.scan_all_button)

        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self._cancel_all)
        layout.addWidget(self.cancel_button)

        cores_row = QHBoxLayout()
        cores_row.addWidget(QLabel("Parallel scans"))
        self.cores_spin = QSpinBox()
        cpu = os.cpu_count() or 1
        self.cores_spin.setRange(1, max(1, cpu))
        self.cores_spin.setValue(self._default_cores())
        self.cores_spin.setToolTip(
            f"How many files to scan at once (1–{max(1, cpu)} cores available)."
        )
        self.cores_spin.valueChanged.connect(self._on_cores_changed)
        cores_row.addWidget(self.cores_spin)
        cores_row.addStretch(1)
        layout.addLayout(cores_row)

        self.total_progress = QProgressBar()
        self.total_progress.setRange(0, 100)
        self.total_progress.setValue(0)
        layout.addWidget(self.total_progress)

        self.eta_label = QLabel("")
        self.eta_label.setStyleSheet("color: gray; font-size: 11px;")
        layout.addWidget(self.eta_label)

        return container

    def _build_config_side(self) -> QWidget:
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(QLabel("<b>Detection settings</b>"))

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self.config_panel = ConfigPanel()
        self.config_panel.changed.connect(self._refresh_all_rows)
        scroll.setWidget(self.config_panel)
        layout.addWidget(scroll, 1)

        return container

    def _build_player_side(self) -> QWidget:
        container = QWidget()
        layout = QVBoxLayout(container)

        self.video_view = VideoView()
        self.video_view.toolReset.connect(self._on_tool_reset)
        self.video_view.regionsChanged.connect(self._on_regions_changed)
        layout.addWidget(self.video_view, 1)

        # Detection-region tool bar.
        region_row = QHBoxLayout()
        region_row.addWidget(QLabel("Region:"))

        self._tool_group = QButtonGroup(self)
        self._tool_group.setExclusive(True)
        self._tool_buttons: dict[str, QPushButton] = {}
        icon_color = self.palette().color(QPalette.ColorRole.ButtonText)
        for tool, name, tip in (
            (TOOL_POINTER, "Pointer",
             "Pointer — drag region corners to adjust them (Ctrl+click deletes a corner)."),
            (TOOL_RECTANGLE, "Rectangle",
             "Rectangle — click two opposite corners to draw a box."),
            (TOOL_POLYGON, "Polygon",
             "Polygon — click each vertex; double-click to close."),
            (TOOL_DELETE, "Delete", "Delete — click a region to remove it."),
        ):
            button = QPushButton()
            button.setIcon(tool_icon(tool, icon_color))
            button.setIconSize(QSize(20, 20))
            button.setCheckable(True)
            button.setToolTip(tip)
            button.setAccessibleName(name)
            button.clicked.connect(lambda _=False, t=tool: self._on_tool_clicked(t))
            self._tool_group.addButton(button)
            self._tool_buttons[tool] = button
            region_row.addWidget(button)
        self._tool_buttons[TOOL_POINTER].setChecked(True)

        self.region_enabled_check = QCheckBox("Enabled")
        self.region_enabled_check.setChecked(True)
        self.region_enabled_check.setToolTip(
            "Uncheck to keep this file's regions but exclude them from the scan."
        )
        self.region_enabled_check.toggled.connect(self._on_region_enabled_toggled)
        region_row.addWidget(self.region_enabled_check)

        region_row.addStretch(1)
        layout.addLayout(region_row)

        self.timeline = TimelineSeekBar()
        self.timeline.seekRequested.connect(self._seek)
        layout.addWidget(self.timeline)

        controls = QHBoxLayout()
        self.play_button = QPushButton()
        self.play_button.setIcon(
            self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPlay)
        )
        self.play_button.clicked.connect(self._toggle_play)
        controls.addWidget(self.play_button)

        self.time_label = QLabel("00:00:00.000 / 00:00:00.000")
        controls.addWidget(self.time_label)
        controls.addStretch(1)

        controls.addWidget(QLabel("Volume"))
        self.volume_slider = QSlider(Qt.Orientation.Horizontal)
        self.volume_slider.setRange(0, 100)
        self.volume_slider.setValue(80)
        self.volume_slider.setFixedWidth(110)
        self.volume_slider.valueChanged.connect(self._on_volume_changed)
        controls.addWidget(self.volume_slider)

        layout.addLayout(controls)

        self.status_label = QLabel("Ready.")
        self.status_label.setStyleSheet("color: gray;")
        layout.addWidget(self.status_label)

        return container

    def _build_results_side(self) -> QWidget:
        container = QWidget()
        layout = QVBoxLayout(container)

        self.results_header = QLabel("<b>Detected events</b>")
        layout.addWidget(self.results_header)

        self.results_table = QTableWidget(0, 3)
        self.results_table.setHorizontalHeaderLabels(["#", "Start", "Duration"])
        self.results_table.verticalHeader().setVisible(False)
        self.results_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.results_table.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection
        )
        self.results_table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers
        )
        header = self.results_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.results_table.itemSelectionChanged.connect(self._on_result_selected)
        self.results_table.itemDoubleClicked.connect(self._on_result_activated)
        layout.addWidget(self.results_table, 1)

        hint = QLabel("Double-click an event to jump and play.")
        hint.setStyleSheet("color: gray; font-size: 11px;")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        return container

    def _build_player(self) -> None:
        self.player = QMediaPlayer(self)
        self.audio = QAudioOutput(self)
        self.audio.setVolume(0.8)
        self.player.setAudioOutput(self.audio)
        self.player.setVideoOutput(self.video_view.video_item)
        self.player.positionChanged.connect(self._on_position_changed)
        self.player.durationChanged.connect(self._on_duration_changed)
        self.player.metaDataChanged.connect(self._on_metadata_changed)
        self.player.playbackStateChanged.connect(self._on_playback_state_changed)
        self.player.errorOccurred.connect(self._on_player_error)

    @staticmethod
    def _hline() -> QFrame:
        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setFrameShadow(QFrame.Shadow.Sunken)
        return line

    @staticmethod
    def _default_cores() -> int:
        # Use about half the cores by default — enough to parallelize without
        # oversubscribing (each dvr-scan process is itself multi-threaded).
        return max(1, (os.cpu_count() or 2) // 2)

    # ---- file list management --------------------------------------------

    def _ordered_paths(self) -> list[str]:
        return [
            self.file_list.item(i).data(PATH_ROLE)
            for i in range(self.file_list.count())
        ]

    def _browse_add(self) -> None:
        start_dir = QStandardPaths.writableLocation(
            QStandardPaths.StandardLocation.MoviesLocation
        )
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Add videos", start_dir, _VIDEO_FILTER
        )
        if paths:
            self._add_files(paths)

    def _add_files(self, paths: list[str]) -> None:
        added: list[str] = []
        duplicates: list[str] = []
        for raw in paths:
            path = raw.strip()
            if not path or not os.path.isfile(path):
                continue
            if path in self._entries:
                duplicates.append(path)
                continue
            entry = FileEntry(path=path)
            self._entries[path] = entry
            item = QListWidgetItem()
            item.setData(PATH_ROLE, path)
            self.file_list.addItem(item)
            widget = FileItemWidget()
            self.file_list.setItemWidget(item, widget)
            self._items[path] = item
            self._refresh_row(path)
            added.append(path)

        if duplicates:
            names = "\n".join(f"• {os.path.basename(d)}" for d in duplicates)
            QMessageBox.information(
                self,
                "Already added",
                f"These file(s) are already in the list:\n\n{names}",
            )

        if added and self.file_list.currentItem() is None:
            self.file_list.setCurrentItem(self._items[added[0]])
        self._update_scan_buttons()

    def _remove_selected(self) -> None:
        item = self.file_list.currentItem()
        if item is None:
            return
        path = item.data(PATH_ROLE)
        self.file_list.takeItem(self.file_list.row(item))
        self._items.pop(path, None)
        self._entries.pop(path, None)
        # currentItemChanged fires from takeItem and re-syncs the player.
        self._update_scan_buttons()

    def _refresh_row(self, path: str) -> None:
        item = self._items.get(path)
        entry = self._entries.get(path)
        if item is None or entry is None:
            return
        widget = self.file_list.itemWidget(item)
        if widget is None:
            return
        widget.update_view(entry, self._needs_update(entry))
        item.setSizeHint(widget.sizeHint())

    def _refresh_all_rows(self) -> None:
        for path in list(self._entries):
            self._refresh_row(path)

    # ---- file selection / playback ---------------------------------------

    def _on_current_changed(
        self, current: QListWidgetItem | None, previous: QListWidgetItem | None
    ) -> None:
        if previous is not None:
            self._save_regions(previous.data(PATH_ROLE))

        if current is None:
            self._current_path = None
            self.player.setSource(QUrl())
            self.video_view.set_regions([])
            self._show_events([])
            self.remove_button.setEnabled(False)
            self.remove_action.setEnabled(False)
            self._update_scan_buttons()
            return

        self._select_file(current.data(PATH_ROLE))

    def _select_file(self, path: str) -> None:
        entry = self._entries.get(path)
        if entry is None:
            return
        self._current_path = path
        self.remove_button.setEnabled(not self._scanning_active)
        self.remove_action.setEnabled(not self._scanning_active)

        self.player.setSource(QUrl.fromLocalFile(path))

        self.region_enabled_check.blockSignals(True)
        self.region_enabled_check.setChecked(entry.region_enabled)
        self.region_enabled_check.blockSignals(False)
        self.video_view.set_region_enabled(entry.region_enabled)
        self._apply_regions_for_current()

        self._show_events(entry.events)
        self._set_status(f"Loaded {entry.name}")
        self._update_scan_buttons()

    def _apply_regions_for_current(self) -> None:
        entry = self._entries.get(self._current_path)
        if entry is None:
            return
        # Only restore once the resolution is known; otherwise clear the view
        # and re-apply from _on_metadata_changed.
        self.video_view.set_regions(entry.regions if self.video_view.can_edit() else [])

    def _save_regions(self, path: str) -> None:
        entry = self._entries.get(path)
        if entry is None or not self.video_view.can_edit():
            return
        entry.regions = self.video_view.regions_points()
        entry.region_enabled = self.video_view.is_region_enabled()

    def _show_events(self, events: list[MotionEvent]) -> None:
        self._events = list(events)
        self._populate_results(self._events)
        self.timeline.set_events(self._events)

    # ---- detection region -------------------------------------------------

    def _on_tool_clicked(self, tool: str) -> None:
        if tool in (TOOL_RECTANGLE, TOOL_POLYGON) and not self.video_view.can_edit():
            self._tool_buttons[TOOL_POINTER].setChecked(True)
            self._set_status("Load a video before drawing a region.")
            return
        self.video_view.set_tool(tool)

    def _on_tool_reset(self) -> None:
        self._tool_buttons[TOOL_POINTER].setChecked(True)

    def _on_regions_changed(self) -> None:
        if self._current_path is None:
            return
        entry = self._entries.get(self._current_path)
        if entry is None:
            return
        entry.regions = self.video_view.regions_points()
        self._refresh_row(self._current_path)

    def _on_region_enabled_toggled(self, enabled: bool) -> None:
        self.video_view.set_region_enabled(enabled)
        if self._current_path is not None:
            entry = self._entries.get(self._current_path)
            if entry is not None:
                entry.region_enabled = enabled
                self._refresh_row(self._current_path)

    # ---- scan options / staleness -----------------------------------------

    def _options_for(self, entry: FileEntry):
        options = self.config_panel.options(entry.path)
        if entry.region_enabled and entry.regions:
            options.regions = entry.regions
        return options

    def _signature_for(self, entry: FileEntry) -> tuple:
        return tuple(self._options_for(entry).to_args())

    def _needs_update(self, entry: FileEntry) -> bool:
        return (
            entry.status == STATUS_DONE
            and entry.scanned_signature is not None
            and entry.scanned_signature != self._signature_for(entry)
        )

    # ---- scanning ---------------------------------------------------------

    def _scan_selected(self) -> None:
        if self._current_path is None:
            QMessageBox.warning(self, "No file", "Select a file to scan first.")
            return
        self._begin_batch([self._current_path])

    def _scan_all(self) -> None:
        to_scan = [
            path
            for path in self._ordered_paths()
            if self._entries[path].status != STATUS_DONE
            or self._needs_update(self._entries[path])
        ]
        if not to_scan:
            self._set_status("All files are already scanned with the current settings.")
            return
        self._begin_batch(to_scan)

    def _begin_batch(self, paths: list[str]) -> None:
        if self._scanner.executable() is None:
            QMessageBox.critical(
                self,
                "dvr-scan not found",
                "Could not find the 'dvr-scan' executable on PATH.\n"
                "Install it with 'pip install dvr-scan'.",
            )
            return
        paths = [p for p in paths if p in self._entries]
        if not paths:
            return

        self._batch = set(paths)
        self._batch_start = time.monotonic()
        self._scanner.set_max_concurrent(self.cores_spin.value())
        self._set_scanning(True)

        for path in paths:
            entry = self._entries[path]
            entry.status = STATUS_QUEUED
            entry.progress = 0
            entry.error = ""
            self._refresh_row(path)
            self._scanner.enqueue(path, self._options_for(entry))

        self._update_total_progress()
        self._set_status(f"Scanning {len(paths)} file(s)…")

    def _cancel_all(self) -> None:
        self._set_status("Cancelling…")
        self._scanner.cancel_all()

    def _set_scanning(self, active: bool) -> None:
        self._scanning_active = active
        self.cancel_button.setEnabled(active)
        self.config_panel.set_enabled(not active)
        self.cores_spin.setEnabled(not active)
        self.add_button.setEnabled(not active)
        self.add_action.setEnabled(not active)
        self.region_enabled_check.setEnabled(not active)
        for button in self._tool_buttons.values():
            button.setEnabled(not active)
        has_selection = self.file_list.currentItem() is not None
        self.remove_button.setEnabled(not active and has_selection)
        self.remove_action.setEnabled(not active and has_selection)
        self._update_scan_buttons()

    def _update_scan_buttons(self) -> None:
        has_exe = self._scanner.executable() is not None
        idle = not self._scanning_active
        self.scan_selected_button.setEnabled(
            idle and has_exe and self._current_path is not None
        )
        self.scan_all_button.setEnabled(
            idle and has_exe and self.file_list.count() > 0
        )

    def _on_cores_changed(self, value: int) -> None:
        self._scanner.set_max_concurrent(value)

    # ---- scan-manager callbacks -------------------------------------------

    def _on_scan_started(self, path: str) -> None:
        entry = self._entries.get(path)
        if entry is not None:
            entry.status = STATUS_RUNNING
            entry.progress = 0
            self._refresh_row(path)
        self._update_total_progress()

    def _on_scan_progress(self, path: str, value: int) -> None:
        entry = self._entries.get(path)
        if entry is not None:
            entry.progress = value
            self._refresh_row(path)
        self._update_total_progress()

    def _on_scan_log(self, path: str, line: str) -> None:
        if line.startswith("[DVR-Scan]"):
            self._set_status(f"{os.path.basename(path)}: {line}")

    def _on_scan_finished(self, path: str, events: list) -> None:
        entry = self._entries.get(path)
        if entry is not None:
            entry.status = STATUS_DONE
            entry.events = events
            entry.progress = 100
            entry.error = ""
            entry.scanned_signature = self._signature_for(entry)
            self._refresh_row(path)
            if path == self._current_path:
                self._show_events(events)
        self._update_total_progress()

    def _on_scan_failed(self, path: str, message: str) -> None:
        entry = self._entries.get(path)
        if entry is not None:
            entry.status = STATUS_FAILED
            entry.error = message
            entry.progress = 0
            self._refresh_row(path)
        self._set_status(f"{os.path.basename(path)}: {message}")
        self._update_total_progress()

    def _on_scan_cancelled(self, path: str) -> None:
        entry = self._entries.get(path)
        if entry is not None:
            # Keep already-completed results; revert an in-flight scan.
            if entry.events:
                entry.status = STATUS_DONE
                entry.progress = 100
            else:
                entry.status = STATUS_IDLE
                entry.progress = 0
            self._refresh_row(path)
        self._update_total_progress()

    def _on_manager_idle(self) -> None:
        self._set_scanning(False)
        batch = self._batch
        done = sum(
            1
            for p in batch
            if self._entries.get(p) and self._entries[p].status == STATUS_DONE
        )
        failed = sum(
            1
            for p in batch
            if self._entries.get(p) and self._entries[p].status == STATUS_FAILED
        )
        self._batch = set()
        self.eta_label.setText("")
        self.total_progress.setValue(0)
        if failed:
            self._set_status(f"Scan finished — {done} done, {failed} failed.")
        else:
            self._set_status(f"Scan complete — {done} file(s) scanned.")

    def _update_total_progress(self) -> None:
        if not self._batch:
            return
        entries = [self._entries[p] for p in self._batch if p in self._entries]
        if not entries:
            return
        total = sum(e.progress for e in entries) / len(entries)
        self.total_progress.setValue(int(round(total)))

        elapsed = time.monotonic() - self._batch_start
        fraction = total / 100.0
        if 0.02 < fraction < 1.0 and elapsed > 0.5:
            remaining = elapsed * (1.0 - fraction) / fraction
            self.eta_label.setText(f"ETA {self._format_duration(remaining)}")
        elif fraction >= 1.0:
            self.eta_label.setText("")
        else:
            self.eta_label.setText("Estimating…")

    @staticmethod
    def _format_duration(seconds: float) -> str:
        seconds = int(round(max(0.0, seconds)))
        hours, seconds = divmod(seconds, 3600)
        minutes, seconds = divmod(seconds, 60)
        if hours:
            return f"{hours}:{minutes:02d}:{seconds:02d}"
        return f"{minutes:02d}:{seconds:02d}"

    # ---- results ----------------------------------------------------------

    def _populate_results(self, events: list[MotionEvent]) -> None:
        self.results_header.setText(f"<b>Detected events ({len(events)})</b>")
        self.results_table.setRowCount(len(events))
        for row, event in enumerate(events):
            index_item = QTableWidgetItem(str(event.index))
            index_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            start_item = QTableWidgetItem(ms_to_timecode(event.start_ms))
            dur_item = QTableWidgetItem(ms_to_timecode(event.duration_ms))
            dur_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.results_table.setItem(row, 0, index_item)
            self.results_table.setItem(row, 1, start_item)
            self.results_table.setItem(row, 2, dur_item)

    def _selected_event(self) -> MotionEvent | None:
        row = self.results_table.currentRow()
        if 0 <= row < len(self._events):
            return self._events[row]
        return None

    def _on_result_selected(self) -> None:
        event = self._selected_event()
        self.timeline.highlight_event(event.index if event else -1)

    def _on_result_activated(self, _item: QTableWidgetItem) -> None:
        event = self._selected_event()
        if event is not None:
            self._seek(event.start_ms)
            self.player.play()

    # ---- player callbacks -------------------------------------------------

    def _seek(self, position_ms: int) -> None:
        self.player.setPosition(int(position_ms))
        self.timeline.set_position(int(position_ms))

    def _toggle_play(self) -> None:
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
        else:
            self.player.play()

    def _on_volume_changed(self, value: int) -> None:
        self.audio.setVolume(value / 100.0)

    def _on_position_changed(self, position_ms: int) -> None:
        self.timeline.set_position(position_ms)
        self._update_time_label(position_ms, self.player.duration())

    def _on_metadata_changed(self) -> None:
        # Feed the source resolution to the view as soon as it's known, so the
        # region editor can map screen coordinates to video pixels, then
        # restore the selected file's saved regions.
        resolution = self.player.metaData().value(QMediaMetaData.Key.Resolution)
        if resolution is not None:
            self.video_view.set_video_size(resolution)
            self._apply_regions_for_current()

    def _on_duration_changed(self, duration_ms: int) -> None:
        self.timeline.set_duration(duration_ms)
        self._update_time_label(self.player.position(), duration_ms)

    def _update_time_label(self, position_ms: int, duration_ms: int) -> None:
        self.time_label.setText(
            f"{ms_to_timecode(position_ms)} / {ms_to_timecode(duration_ms)}"
        )

    def _on_playback_state_changed(self, state: QMediaPlayer.PlaybackState) -> None:
        playing = state == QMediaPlayer.PlaybackState.PlayingState
        icon = QStyle.StandardPixmap.SP_MediaPause if playing else QStyle.StandardPixmap.SP_MediaPlay
        self.play_button.setIcon(self.style().standardIcon(icon))

    def _on_player_error(self, _error, error_string: str) -> None:
        if error_string:
            self._set_status(f"Player error: {error_string}")

    # ---- misc -------------------------------------------------------------

    def _set_status(self, text: str) -> None:
        self.status_label.setText(text)

    def closeEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        self._scanner.cancel_all()
        super().closeEvent(event)
