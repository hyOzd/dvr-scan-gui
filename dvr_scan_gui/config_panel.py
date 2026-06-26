"""The DVR-Scan configuration form (the basic, most-used options)."""

from __future__ import annotations

import os

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QLabel,
    QLineEdit,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from .scanner import ScanOptions


class ConfigPanel(QWidget):
    """Exposes the common DVR-Scan detection parameters as a form."""

    # Emitted whenever any detection option changes, so already-scanned files
    # can be flagged as needing a re-scan with the new settings.
    changed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        # --- Detection sensitivity -------------------------------------
        detection = QGroupBox("Detection")
        det_form = QFormLayout(detection)

        self.threshold = QDoubleSpinBox()
        self.threshold.setRange(0.0, 5.0)
        self.threshold.setSingleStep(0.01)
        self.threshold.setDecimals(3)
        self.threshold.setValue(0.15)
        self.threshold.setToolTip(
            "Amount of motion required to trigger an event. Lower = more sensitive."
        )
        det_form.addRow("Threshold", self.threshold)

        self.bg_subtractor = QComboBox()
        self.bg_subtractor.addItems(["MOG2", "CNT"])
        self.bg_subtractor.setToolTip("Background subtraction algorithm.")
        det_form.addRow("BG subtractor", self.bg_subtractor)

        self.kernel_size = QSpinBox()
        self.kernel_size.setRange(-1, 99)
        self.kernel_size.setValue(-1)
        self.kernel_size.setToolTip(
            "Noise-reduction kernel size (odd). -1 auto, 0 disables."
        )
        det_form.addRow("Kernel size", self.kernel_size)

        layout.addWidget(detection)

        # --- Event timing ----------------------------------------------
        timing = QGroupBox("Event timing")
        timing_form = QFormLayout(timing)

        self.min_event_length = QLineEdit("0.1s")
        self.min_event_length.setToolTip("Minimum motion duration to start an event.")
        timing_form.addRow("Min length", self.min_event_length)

        self.time_before = QLineEdit("1.5s")
        self.time_before.setToolTip("Padding included before each event.")
        timing_form.addRow("Time before", self.time_before)

        self.time_post = QLineEdit("2.0s")
        self.time_post.setToolTip("Trailing time of no motion that ends an event.")
        timing_form.addRow("Time after", self.time_post)

        layout.addWidget(timing)

        # --- Range (optional) ------------------------------------------
        range_box = QGroupBox("Scan range (optional)")
        range_form = QFormLayout(range_box)

        hint = QLabel("Frames (123), seconds (12.3s), or timecode (00:01:02).")
        hint.setWordWrap(True)
        hint.setStyleSheet("color: gray; font-size: 11px;")
        range_form.addRow(hint)

        self.start_time = QLineEdit()
        self.start_time.setPlaceholderText("from start")
        range_form.addRow("Start", self.start_time)

        self.end_time = QLineEdit()
        self.end_time.setPlaceholderText("to end")
        range_form.addRow("End", self.end_time)

        self.duration = QLineEdit()
        self.duration.setPlaceholderText("overrides End")
        range_form.addRow("Duration", self.duration)

        layout.addWidget(range_box)

        # --- Performance -----------------------------------------------
        perf = QGroupBox("Performance")
        perf_form = QFormLayout(perf)

        self.downscale = QSpinBox()
        self.downscale.setRange(0, 16)
        self.downscale.setValue(0)
        self.downscale.setToolTip("Shrink frames before processing. 0 disables.")
        perf_form.addRow("Downscale", self.downscale)

        self.frame_skip = QSpinBox()
        self.frame_skip.setRange(0, 60)
        self.frame_skip.setValue(0)
        self.frame_skip.setToolTip("Frames to skip between processed frames.")
        perf_form.addRow("Frame skip", self.frame_skip)

        layout.addWidget(perf)

        # --- Scanning (how many files to process at once) --------------
        scanning = QGroupBox("Scanning")
        scanning_form = QFormLayout(scanning)

        self.cores_spin = QSpinBox()
        cpu = os.cpu_count() or 1
        self.cores_spin.setRange(1, max(1, cpu))
        # About half the cores by default — enough to parallelize without
        # oversubscribing (each dvr-scan process is itself multi-threaded).
        self.cores_spin.setValue(max(1, cpu // 2))
        self.cores_spin.setToolTip(
            f"How many files to scan at once (1–{max(1, cpu)} cores available)."
        )
        scanning_form.addRow("Parallel scans", self.cores_spin)

        layout.addWidget(scanning)
        layout.addStretch(1)

        # Re-emit a single `changed` signal whenever any option is edited.
        for spin in (self.threshold, self.kernel_size, self.downscale, self.frame_skip):
            spin.valueChanged.connect(self.changed)
        self.bg_subtractor.currentIndexChanged.connect(self.changed)
        for edit in (
            self.min_event_length,
            self.time_before,
            self.time_post,
            self.start_time,
            self.end_time,
            self.duration,
        ):
            edit.textChanged.connect(self.changed)

    def options(self, input_path: str) -> ScanOptions:
        """Build a :class:`ScanOptions` from the current form values."""
        return ScanOptions(
            input_path=input_path,
            threshold=self.threshold.value(),
            min_event_length=self.min_event_length.text() or "0.1s",
            time_before_event=self.time_before.text() or "1.5s",
            time_post_event=self.time_post.text() or "2.0s",
            bg_subtractor=self.bg_subtractor.currentText(),
            kernel_size=self.kernel_size.value(),
            downscale_factor=self.downscale.value(),
            frame_skip=self.frame_skip.value(),
            start_time=self.start_time.text(),
            end_time=self.end_time.text(),
            duration=self.duration.text(),
        )

    def set_enabled(self, enabled: bool) -> None:
        self.setEnabled(enabled)
