"""The multi-file list: per-file scan state, a rich row widget, and a
drag-and-drop-capable list.

Each video the user adds is tracked by a :class:`FileEntry` (its scan status,
last results, progress, and the per-file detection regions). The list widget
shows one :class:`FileItemWidget` per file: the file name, a status badge with
the detection count, and a slim progress bar tucked under the name.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QListWidget,
    QProgressBar,
    QVBoxLayout,
    QWidget,
)

from .scanner import MotionEvent

# Scan lifecycle states for a single file.
STATUS_IDLE = "idle"          # never scanned
STATUS_QUEUED = "queued"      # waiting for a free scan slot
STATUS_RUNNING = "running"    # being scanned right now
STATUS_DONE = "done"          # scanned successfully
STATUS_FAILED = "failed"      # the scan errored out

# The file path is stored on each list item under this role.
PATH_ROLE = Qt.ItemDataRole.UserRole


@dataclass
class FileEntry:
    """Everything we track about one input video."""

    path: str
    status: str = STATUS_IDLE
    progress: int = 0
    events: list[MotionEvent] = field(default_factory=list)
    error: str = ""
    # Signature (tuple of dvr-scan args) the current results were produced with;
    # if it differs from the file's current signature, the results are stale.
    scanned_signature: tuple | None = None
    # Per-file detection regions (each a list of integer (x, y) vertices) and
    # whether they apply to the scan.
    regions: list = field(default_factory=list)
    region_enabled: bool = True

    @property
    def name(self) -> str:
        return os.path.basename(self.path)


_STATUS_TEXT = {
    STATUS_IDLE: ("Not scanned", "#9aa0a6"),
    STATUS_QUEUED: ("Queued", "#c8a23b"),
    STATUS_RUNNING: ("Scanning…", "#5a82c8"),
    STATUS_DONE: ("Done", "#4caf6d"),
    STATUS_FAILED: ("Failed", "#e0604a"),
}
_NEEDS_UPDATE = ("Needs update", "#d98a2b")


class FileItemWidget(QWidget):
    """The row widget shown for one file in the list."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 5, 8, 5)
        outer.setSpacing(3)

        top = QHBoxLayout()
        top.setSpacing(6)
        self.name_label = QLabel()
        self.name_label.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        font = self.name_label.font()
        font.setPointSizeF(font.pointSizeF() + 0.5)
        self.name_label.setFont(font)
        top.addWidget(self.name_label, 1)

        self.count_label = QLabel()
        self.count_label.setStyleSheet("color: #8a8f98; font-size: 11px;")
        top.addWidget(self.count_label, 0, Qt.AlignmentFlag.AlignRight)

        self.status_label = QLabel()
        self.status_label.setStyleSheet("font-size: 11px;")
        top.addWidget(self.status_label, 0, Qt.AlignmentFlag.AlignRight)
        outer.addLayout(top)

        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(4)
        self.progress.setStyleSheet(
            "QProgressBar { background: rgba(127,127,127,0.25); border: none;"
            " border-radius: 2px; }"
            "QProgressBar::chunk { background: #5a82c8; border-radius: 2px; }"
        )
        outer.addWidget(self.progress)

    def update_view(self, entry: FileEntry, needs_update: bool) -> None:
        self.name_label.setText(entry.name)
        self.name_label.setToolTip(entry.path)

        if entry.status == STATUS_DONE and needs_update:
            text, color = _NEEDS_UPDATE
        else:
            text, color = _STATUS_TEXT.get(entry.status, _STATUS_TEXT[STATUS_IDLE])
        if entry.status == STATUS_FAILED and entry.error:
            self.status_label.setToolTip(entry.error)
        else:
            self.status_label.setToolTip("")
        self.status_label.setText(text)
        self.status_label.setStyleSheet(f"font-size: 11px; color: {color};")

        # Detection count, shown once the file has been scanned.
        if entry.status in (STATUS_DONE,) or entry.events:
            n = len(entry.events)
            self.count_label.setText(f"{n} event{'' if n == 1 else 's'}")
        else:
            self.count_label.setText("")

        # The slim bar is only meaningful while queued or running.
        if entry.status in (STATUS_QUEUED, STATUS_RUNNING):
            self.progress.setVisible(True)
            self.progress.setValue(0 if entry.status == STATUS_QUEUED else entry.progress)
        else:
            self.progress.setVisible(False)


class FileListWidget(QListWidget):
    """A list of input files that also accepts dropped video files.

    Signals:
        filesDropped(list): local file paths dropped onto the list.
    """

    filesDropped = Signal(list)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setDragDropMode(QListWidget.DragDropMode.DropOnly)
        self.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        self.setAlternatingRowColors(True)
        self.setUniformItemSizes(False)

    # ---- drag and drop ----------------------------------------------------

    def _has_local_files(self, event) -> bool:
        mime = event.mimeData()
        return mime.hasUrls() and any(u.isLocalFile() for u in mime.urls())

    def dragEnterEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        if self._has_local_files(event):
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event) -> None:  # noqa: N802
        if self._has_local_files(event):
            event.acceptProposedAction()
        else:
            super().dragMoveEvent(event)

    def dropEvent(self, event) -> None:  # noqa: N802
        if not self._has_local_files(event):
            super().dropEvent(event)
            return
        paths = [
            u.toLocalFile()
            for u in event.mimeData().urls()
            if u.isLocalFile() and os.path.isfile(u.toLocalFile())
        ]
        if paths:
            self.filesDropped.emit(paths)
            event.acceptProposedAction()
