"""A custom horizontal seek bar that overlays detected motion events and lets
the user pick a per-file scan range.

It behaves like a normal player scrubber (click or drag to seek) but also
paints every detected motion event as a colored band on the track, so the
distribution of motion across the video is visible at a glance.

Two draggable handles select the scan range (start and end). Clicking a handle
pops up a small editable label above it showing that bound's timecode; the
user can drag the handle or type a time directly into the label. The portion of
the track outside the selected range is dimmed.
"""

from __future__ import annotations

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QColor, QMouseEvent, QPainter, QPaintEvent
from PySide6.QtWidgets import QLineEdit, QSizePolicy, QWidget

from .scanner import MotionEvent, ms_to_timecode


class TimelineSeekBar(QWidget):
    """Seek bar with motion-event overlays and scan-range handles.

    Signals:
        seekRequested(int): emitted with a millisecond position when the user
            clicks or drags on the track (not on a range handle).
        rangeChanged(int, int): emitted with the effective (start_ms, end_ms)
            of the scan range whenever the user moves a handle or edits a label.
            The values span the whole clip when the range is unbounded.
    """

    seekRequested = Signal(int)
    rangeChanged = Signal(int, int)

    _TRACK_COLOR = QColor(60, 60, 66)
    _PLAYED_COLOR = QColor(90, 130, 200)
    _EVENT_COLOR = QColor(232, 120, 70, 200)
    _EVENT_HILITE_COLOR = QColor(255, 196, 0, 230)
    _PLAYHEAD_COLOR = QColor(245, 245, 245)
    _EXCLUDED_COLOR = QColor(18, 18, 22, 165)
    _START_COLOR = QColor(80, 200, 120)
    _END_COLOR = QColor(232, 110, 90)

    _LABEL_H = 18
    _HANDLE_HALF = 7  # px hit radius around a handle

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._duration_ms = 0
        self._position_ms = 0
        self._events: list[MotionEvent] = []
        self._highlighted = -1
        # Scan range; None = unbounded on that side (file start / file end).
        self._range_start_ms: int | None = None
        self._range_end_ms: int | None = None
        self._drag: str | None = None      # handle being dragged: 'start'/'end'
        self._active: str | None = None    # handle whose editor is shown
        self._committing = False           # re-entrancy guard for editors
        self.setMinimumHeight(58)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        self._start_edit = self._make_editor(self._START_COLOR)
        self._end_edit = self._make_editor(self._END_COLOR)
        self._start_edit.editingFinished.connect(lambda: self._commit_editor("start"))
        self._end_edit.editingFinished.connect(lambda: self._commit_editor("end"))

    def _make_editor(self, accent: QColor) -> QLineEdit:
        edit = QLineEdit(self)
        edit.setVisible(False)
        edit.setAlignment(Qt.AlignmentFlag.AlignCenter)
        edit.setFixedHeight(self._LABEL_H)
        edit.setFixedWidth(94)
        edit.setToolTip("Type a time (SS, MM:SS, or HH:MM:SS).")
        edit.setStyleSheet(
            "QLineEdit { background: #1e1e22; color: #f0f0f0; border: 1px solid %s;"
            " border-radius: 3px; padding: 0 2px; font-size: 11px; }" % accent.name()
        )
        return edit

    # ---- public API -------------------------------------------------------

    def set_duration(self, duration_ms: int) -> None:
        self._duration_ms = max(0, int(duration_ms))
        # Keep any bounded range inside the (possibly new) clip length.
        if self._range_start_ms is not None:
            self._range_start_ms = min(self._range_start_ms, self._duration_ms)
        if self._range_end_ms is not None:
            self._range_end_ms = min(self._range_end_ms, self._duration_ms)
        self._reposition_editors()
        self.update()

    def duration_ms(self) -> int:
        return self._duration_ms

    def set_position(self, position_ms: int) -> None:
        self._position_ms = max(0, int(position_ms))
        self.update()

    def set_events(self, events: list[MotionEvent]) -> None:
        self._events = list(events)
        self._highlighted = -1
        self.update()

    def highlight_event(self, index: int) -> None:
        """Highlight the event with the given 1-based index (-1 to clear)."""
        self._highlighted = index
        self.update()

    def set_range(self, start_ms: int | None, end_ms: int | None) -> None:
        """Set the scan range (None on either side means unbounded)."""
        self._range_start_ms = None if not start_ms else max(0, int(start_ms))
        self._range_end_ms = None if end_ms is None else max(0, int(end_ms))
        if self._duration_ms > 0:
            if self._range_start_ms is not None:
                self._range_start_ms = min(self._range_start_ms, self._duration_ms)
            if self._range_end_ms is not None:
                self._range_end_ms = min(self._range_end_ms, self._duration_ms)
        self._drag = None
        self._set_active(None)
        self.update()

    # ---- range helpers ----------------------------------------------------

    def _eff_start(self) -> int:
        return self._range_start_ms or 0

    def _eff_end(self) -> int:
        return self._range_end_ms if self._range_end_ms is not None else self._duration_ms

    def _emit_range(self) -> None:
        self.rangeChanged.emit(self._eff_start(), self._eff_end())

    # ---- geometry helpers -------------------------------------------------

    def _track_rect(self) -> QRectF:
        margin = 8.0
        height = 16.0
        top = self._LABEL_H + (self.height() - self._LABEL_H - height) / 2
        return QRectF(margin, top, max(0.0, self.width() - 2 * margin), height)

    def _x_to_ms(self, x: float) -> int:
        track = self._track_rect()
        if track.width() <= 0 or self._duration_ms <= 0:
            return 0
        ratio = (x - track.left()) / track.width()
        return int(round(max(0.0, min(1.0, ratio)) * self._duration_ms))

    def _ms_to_x(self, ms: int) -> float:
        track = self._track_rect()
        if self._duration_ms <= 0:
            return track.left()
        ratio = max(0.0, min(1.0, ms / self._duration_ms))
        return track.left() + ratio * track.width()

    # ---- painting ---------------------------------------------------------

    def paintEvent(self, _event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        track = self._track_rect()
        radius = track.height() / 2

        # Base track.
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(self._TRACK_COLOR)
        painter.drawRoundedRect(track, radius, radius)

        # Played portion.
        if self._duration_ms > 0:
            played = QRectF(track)
            played.setRight(self._ms_to_x(self._position_ms))
            painter.setBrush(self._PLAYED_COLOR)
            painter.drawRoundedRect(played, radius, radius)

        # Motion-event bands.
        for event in self._events:
            left = self._ms_to_x(event.start_ms)
            right = self._ms_to_x(event.end_ms)
            band = QRectF(left, track.top(), max(2.0, right - left), track.height())
            is_hi = event.index == self._highlighted
            painter.setBrush(self._EVENT_HILITE_COLOR if is_hi else self._EVENT_COLOR)
            painter.drawRoundedRect(band, 2.0, 2.0)

        # Dim everything outside the selected scan range, plus the handles.
        if self._duration_ms > 0:
            self._paint_range(painter, track)

        # Playhead.
        if self._duration_ms > 0:
            x = self._ms_to_x(self._position_ms)
            painter.setPen(self._PLAYHEAD_COLOR)
            painter.setBrush(self._PLAYHEAD_COLOR)
            painter.drawRect(QRectF(x - 1.0, track.top() - 4, 2.0, track.height() + 8))

    def _paint_range(self, painter: QPainter, track: QRectF) -> None:
        start_x = self._ms_to_x(self._eff_start())
        end_x = self._ms_to_x(self._eff_end())

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(self._EXCLUDED_COLOR)
        if start_x > track.left():
            painter.drawRect(QRectF(track.left(), track.top(),
                                    start_x - track.left(), track.height()))
        if end_x < track.right():
            painter.drawRect(QRectF(end_x, track.top(),
                                    track.right() - end_x, track.height()))

        self._paint_handle(painter, start_x, self._START_COLOR)
        self._paint_handle(painter, end_x, self._END_COLOR)

    def _paint_handle(self, painter: QPainter, x: float, color: QColor) -> None:
        track = self._track_rect()
        top = track.top() - 5
        bottom = track.bottom() + 5
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(color)
        painter.drawRoundedRect(QRectF(x - 3.0, top, 6.0, bottom - top), 3.0, 3.0)

    # ---- editable labels --------------------------------------------------

    def _editor(self, which: str) -> QLineEdit:
        return self._start_edit if which == "start" else self._end_edit

    def _set_active(self, which: str | None) -> None:
        self._active = which
        self._start_edit.setVisible(which == "start")
        self._end_edit.setVisible(which == "end")
        if which is not None:
            self._update_editor_text(which)
            self._reposition_editors()

    def _update_editor_text(self, which: str) -> None:
        ms = self._eff_start() if which == "start" else self._eff_end()
        self._editor(which).setText(ms_to_timecode(ms))

    def _reposition_editors(self) -> None:
        for which in ("start", "end"):
            edit = self._editor(which)
            if not edit.isVisible():
                continue
            ms = self._eff_start() if which == "start" else self._eff_end()
            x = self._ms_to_x(ms) - edit.width() / 2
            x = max(0.0, min(x, self.width() - edit.width()))
            edit.move(int(x), 0)
            edit.raise_()

    def _commit_editor(self, which: str) -> None:
        if self._committing:
            return
        self._committing = True
        try:
            ms = self._parse_time(self._editor(which).text())
            if ms is not None:
                if which == "start":
                    ms = max(0, min(ms, self._eff_end()))
                    self._range_start_ms = None if ms <= 0 else ms
                else:
                    if self._duration_ms > 0:
                        ms = min(ms, self._duration_ms)
                    ms = max(ms, self._eff_start())
                    self._range_end_ms = (
                        None if self._duration_ms > 0 and ms >= self._duration_ms else ms
                    )
                self._emit_range()
            self._set_active(None)
            self.update()
        finally:
            self._committing = False

    @staticmethod
    def _parse_time(text: str) -> int | None:
        """Parse 'SS', 'MM:SS', 'HH:MM:SS(.mmm)', or a plain/seconds value."""
        token = text.strip().lower().rstrip("s").strip()
        if not token:
            return None
        try:
            seconds = 0.0
            for part in token.split(":"):
                seconds = seconds * 60 + float(part)
        except ValueError:
            return None
        return int(round(seconds * 1000))

    # ---- interaction ------------------------------------------------------

    def _seek_to_x(self, x: float) -> None:
        if self._duration_ms > 0:
            self.seekRequested.emit(self._x_to_ms(x))

    def _handle_at(self, x: float, y: float) -> str | None:
        if self._duration_ms <= 0:
            return None
        track = self._track_rect()
        if not (track.top() - 8 <= y <= track.bottom() + 8):
            return None
        start_dx = abs(x - self._ms_to_x(self._eff_start()))
        end_dx = abs(x - self._ms_to_x(self._eff_end()))
        if min(start_dx, end_dx) > self._HANDLE_HALF:
            return None
        return "start" if start_dx <= end_dx else "end"

    def _move_handle(self, which: str, x: float) -> None:
        ms = self._x_to_ms(x)
        if which == "start":
            ms = max(0, min(ms, self._eff_end()))
            self._range_start_ms = None if ms <= 0 else ms
        else:
            ms = min(self._duration_ms, max(ms, self._eff_start()))
            self._range_end_ms = None if ms >= self._duration_ms else ms
        self._update_editor_text(which)
        self._reposition_editors()
        self.update()
        self._emit_range()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            return
        pos = event.position()
        handle = self._handle_at(pos.x(), pos.y())
        if handle is not None:
            self._drag = handle
            self._set_active(handle)
            return
        self._set_active(None)
        self._seek_to_x(pos.x())

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        pos = event.position()
        if self._drag is not None:
            self._move_handle(self._drag, pos.x())
            return
        if event.buttons() & Qt.MouseButton.LeftButton:
            self._seek_to_x(pos.x())
        if self._handle_at(pos.x(), pos.y()) is not None:
            self.setCursor(Qt.CursorShape.SplitHCursor)
        else:
            self.setCursor(Qt.CursorShape.PointingHandCursor)
        if self._duration_ms > 0:
            self.setToolTip(ms_to_timecode(self._x_to_ms(pos.x())))

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if self._drag is not None:
            which = self._drag
            self._drag = None
            # Hand focus to the label so the bound can be typed precisely.
            editor = self._editor(which)
            editor.setFocus(Qt.FocusReason.MouseFocusReason)
            editor.selectAll()

    def resizeEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        super().resizeEvent(event)
        self._reposition_editors()
