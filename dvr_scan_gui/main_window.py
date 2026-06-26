"""The application's main window."""

from __future__ import annotations

import os

from PySide6.QtCore import QSize, QStandardPaths, Qt, QUrl
from PySide6.QtGui import QAction, QKeySequence, QPalette
from PySide6.QtMultimedia import QAudioOutput, QMediaMetaData, QMediaPlayer
from PySide6.QtWidgets import (
    QAbstractItemView,
    QButtonGroup,
    QCheckBox,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSlider,
    QSplitter,
    QStyle,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .config_panel import ConfigPanel
from .icons import tool_icon
from .scanner import MotionEvent, Scanner, ms_to_timecode
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
        self.resize(1180, 720)

        self._events: list[MotionEvent] = []
        self._scanner = Scanner(self)
        self._scanner.progress.connect(self._on_progress)
        self._scanner.log.connect(self._append_log)
        self._scanner.finished.connect(self._on_scan_finished)
        self._scanner.failed.connect(self._on_scan_failed)

        self._build_ui()
        self._build_player()

        if self._scanner.executable() is None:
            self._set_status(
                "dvr-scan not found on PATH — install it with 'pip install dvr-scan'."
            )

    # ---- UI construction --------------------------------------------------

    def _build_ui(self) -> None:
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self._build_config_side())
        splitter.addWidget(self._build_player_side())
        splitter.addWidget(self._build_results_side())
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setStretchFactor(2, 0)
        splitter.setSizes([320, 600, 260])
        self.setCentralWidget(splitter)

        open_action = QAction("&Open video…", self)
        open_action.setShortcut(QKeySequence.StandardKey.Open)
        open_action.triggered.connect(self._browse_input)
        self.menuBar().addMenu("&File").addAction(open_action)

    def _build_config_side(self) -> QWidget:
        container = QWidget()
        layout = QVBoxLayout(container)

        layout.addWidget(QLabel("<b>Input video</b>"))
        input_row = QHBoxLayout()
        self.input_edit = QLineEdit()
        self.input_edit.setPlaceholderText("Choose a video file…")
        self.input_edit.editingFinished.connect(
            lambda: self._load_input(self.input_edit.text())
        )
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse_input)
        input_row.addWidget(self.input_edit, 1)
        input_row.addWidget(browse)
        layout.addLayout(input_row)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self.config_panel = ConfigPanel()
        scroll.setWidget(self.config_panel)
        layout.addWidget(scroll, 1)

        self.scan_button = QPushButton("Scan for motion")
        self.scan_button.setDefault(True)
        self.scan_button.setEnabled(False)  # until a valid video is loaded
        self.scan_button.clicked.connect(self._start_scan)
        layout.addWidget(self.scan_button)

        self.cancel_button = QPushButton("Cancel scan")
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self._scanner.cancel)
        layout.addWidget(self.cancel_button)

        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        layout.addWidget(self.progress)

        return container

    def _build_player_side(self) -> QWidget:
        container = QWidget()
        layout = QVBoxLayout(container)

        self.video_view = VideoView()
        self.video_view.toolReset.connect(self._on_tool_reset)
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
            "Uncheck to keep the regions but exclude them from the next scan."
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

    # ---- input handling ---------------------------------------------------

    def _browse_input(self) -> None:
        start_dir = QStandardPaths.writableLocation(
            QStandardPaths.StandardLocation.MoviesLocation
        )
        path, _ = QFileDialog.getOpenFileName(
            self, "Open video", start_dir, _VIDEO_FILTER
        )
        if path:
            self._load_input(path)

    def _load_input(self, path: str) -> None:
        path = path.strip()
        if not path:
            return
        if not os.path.isfile(path):
            self._set_status(f"File not found: {path}")
            return
        self.input_edit.setText(path)
        self.player.setSource(QUrl.fromLocalFile(path))
        self.scan_button.setEnabled(True)
        self._set_status(f"Loaded {os.path.basename(path)}")

    # ---- scanning ---------------------------------------------------------

    def _start_scan(self) -> None:
        path = self.input_edit.text().strip()
        if not path or not os.path.isfile(path):
            QMessageBox.warning(
                self, "No input", "Please choose a valid input video first."
            )
            return
        self._load_input(path)
        options = self.config_panel.options(path)
        if self.video_view.is_region_enabled() and self.video_view.has_regions():
            options.regions = self.video_view.regions_points()
        self.progress.setValue(0)
        self._set_scanning(True)
        self._set_status("Scanning for motion…")
        self._scanner.start(options)

    def _set_scanning(self, scanning: bool) -> None:
        self.scan_button.setEnabled(not scanning)
        self.cancel_button.setEnabled(scanning)
        self.config_panel.set_enabled(not scanning)
        self.input_edit.setEnabled(not scanning)
        self.region_enabled_check.setEnabled(not scanning)
        for button in self._tool_buttons.values():
            button.setEnabled(not scanning)

    # ---- detection region -------------------------------------------------

    def _on_tool_clicked(self, tool: str) -> None:
        if tool in (TOOL_RECTANGLE, TOOL_POLYGON) and not self.video_view.can_edit():
            self._tool_buttons[TOOL_POINTER].setChecked(True)
            self._set_status("Load a video before drawing a region.")
            return
        self.video_view.set_tool(tool)

    def _on_tool_reset(self) -> None:
        # The view finished a drawing (or Esc) — reflect the pointer tool.
        self._tool_buttons[TOOL_POINTER].setChecked(True)

    def _on_region_enabled_toggled(self, enabled: bool) -> None:
        self.video_view.set_region_enabled(enabled)

    def _on_progress(self, value: int) -> None:
        self.progress.setValue(value)

    def _on_scan_finished(self, events: list[MotionEvent]) -> None:
        self._set_scanning(False)
        self._events = events
        self._populate_results(events)
        self.timeline.set_events(events)
        if events:
            self._set_status(f"Found {len(events)} motion event(s).")
        else:
            self._set_status("Scan complete — no motion detected.")

    def _on_scan_failed(self, message: str) -> None:
        self._set_scanning(False)
        self._set_status(message)
        QMessageBox.critical(self, "Scan failed", message)

    def _append_log(self, line: str) -> None:
        # Surface the most recent informative dvr-scan line in the status bar.
        if line.startswith("[DVR-Scan]"):
            self._set_status(line)

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
        # region editor can map screen coordinates to video pixels.
        resolution = self.player.metaData().value(QMediaMetaData.Key.Resolution)
        if resolution is not None:
            self.video_view.set_video_size(resolution)

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
        self._scanner.cancel()
        super().closeEvent(event)
