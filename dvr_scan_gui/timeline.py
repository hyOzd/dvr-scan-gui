"""A custom horizontal seek bar that overlays detected motion events.

It behaves like a normal player scrubber (click or drag to seek) but also
paints every detected motion event as a colored band on the track, so the
distribution of motion across the video is visible at a glance.
"""

from __future__ import annotations

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QColor, QMouseEvent, QPainter, QPaintEvent
from PySide6.QtWidgets import QSizePolicy, QWidget

from .scanner import MotionEvent, ms_to_timecode


class TimelineSeekBar(QWidget):
    """Seek bar with motion-event overlays.

    Signals:
        seekRequested(int): emitted with a millisecond position when the user
            clicks or drags on the bar.
    """

    seekRequested = Signal(int)

    _TRACK_COLOR = QColor(60, 60, 66)
    _PLAYED_COLOR = QColor(90, 130, 200)
    _EVENT_COLOR = QColor(232, 120, 70, 200)
    _EVENT_HILITE_COLOR = QColor(255, 196, 0, 230)
    _PLAYHEAD_COLOR = QColor(245, 245, 245)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._duration_ms = 0
        self._position_ms = 0
        self._events: list[MotionEvent] = []
        self._highlighted = -1
        self.setMinimumHeight(46)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    # ---- public API -------------------------------------------------------

    def set_duration(self, duration_ms: int) -> None:
        self._duration_ms = max(0, int(duration_ms))
        self.update()

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

    # ---- geometry helpers -------------------------------------------------

    def _track_rect(self) -> QRectF:
        margin = 8.0
        height = 16.0
        top = (self.height() - height) / 2
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

        # Playhead.
        if self._duration_ms > 0:
            x = self._ms_to_x(self._position_ms)
            painter.setPen(self._PLAYHEAD_COLOR)
            painter.setBrush(self._PLAYHEAD_COLOR)
            painter.drawRect(QRectF(x - 1.0, track.top() - 4, 2.0, track.height() + 8))

    # ---- interaction ------------------------------------------------------

    def _seek_to_x(self, x: float) -> None:
        if self._duration_ms > 0:
            self.seekRequested.emit(self._x_to_ms(x))

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._seek_to_x(event.position().x())

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if event.buttons() & Qt.MouseButton.LeftButton:
            self._seek_to_x(event.position().x())
        # Tooltip with the timecode under the cursor.
        if self._duration_ms > 0:
            self.setToolTip(ms_to_timecode(self._x_to_ms(event.position().x())))
