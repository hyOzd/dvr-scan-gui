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

import math
from datetime import datetime, timedelta

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontMetrics,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPaintEvent,
    QPen,
    QPolygonF,
)
from PySide6.QtWidgets import QLineEdit, QSizePolicy, QWidget

from .scanner import MotionEvent, ms_to_realtime, ms_to_timecode


class _HoverReadout(QWidget):
    """A small tooltip-style bubble with a downward tail at its bottom-centre.

    Used as the seek-bar's hover read-out: the tail points down at the track so
    it reads like a caret marking the position under the cursor.
    """

    _TAIL_H = 5.0     # height of the pointer triangle
    _TAIL_HALF = 5.0  # half-width of the pointer triangle's base
    _PAD_X = 6.0
    _PAD_Y = 2.0
    _RADIUS = 3.0
    _BG = QColor(0x1E, 0x1E, 0x22)
    _BORDER = QColor(245, 245, 245, 128)
    _FG = QColor(0xF0, 0xF0, 0xF0)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setVisible(False)
        self._text = ""
        font = self.font()
        font.setPixelSize(11)
        self.setFont(font)

    def set_text(self, text: str) -> None:
        if text == self._text:
            return
        self._text = text
        fm = self.fontMetrics()
        w = fm.horizontalAdvance(text) + 2 * self._PAD_X + 2
        h = fm.height() + 2 * self._PAD_Y + 2 + self._TAIL_H
        self.resize(int(math.ceil(w)), int(math.ceil(h)))
        self.update()

    def paintEvent(self, _event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        body_h = self.height() - self._TAIL_H
        body = QRectF(0.5, 0.5, self.width() - 1.0, body_h - 1.0)
        path = QPainterPath()
        path.addRoundedRect(body, self._RADIUS, self._RADIUS)
        cx = self.width() / 2.0
        tail = QPolygonF([
            QPointF(cx - self._TAIL_HALF, body_h - 1.0),
            QPointF(cx + self._TAIL_HALF, body_h - 1.0),
            QPointF(cx, self.height() - 0.5),
        ])
        tail_path = QPainterPath()
        tail_path.addPolygon(tail)
        outline = path.united(tail_path)

        painter.setBrush(self._BG)
        pen = QPen(self._BORDER)
        pen.setWidthF(1.0)
        painter.setPen(pen)
        painter.drawPath(outline)

        painter.setPen(self._FG)
        painter.drawText(
            QRectF(0, 0, self.width(), body_h),
            Qt.AlignmentFlag.AlignCenter,
            self._text,
        )


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
    _COVERAGE_COLOR = QColor(120, 170, 235)

    _LABEL_H = 18
    _HANDLE_HALF = 7  # px hit radius around a handle

    # Timeline tick levels, coarsest first. A level is only drawn when its
    # interval is long enough that consecutive ticks stay at least
    # ``_TICK_MIN_PX`` apart, so short clips never sprout levels they don't
    # need (a same-day clip shows no 'day' ticks, and so on).
    _TICK_LEVELS = ("day", "hour", "q", "min")
    _TICK_INTERVAL = {"day": 86_400_000, "hour": 3_600_000, "q": 900_000, "min": 60_000}
    _TICK_MIN_PX = {"day": 8.0, "hour": 8.0, "q": 10.0, "min": 7.0}
    _TICK_LEN = {"day": 12.0, "hour": 9.0, "q": 6.0, "min": 4.0}
    _TICK_ORDER = {"day": 0, "hour": 1, "q": 2, "min": 3}
    _TICK_COLOR = {
        "day": QColor(205, 205, 215),
        "hour": QColor(175, 175, 190),
        "q": QColor(135, 135, 150),
        "min": QColor(105, 105, 120),
    }
    _TICK_LABEL_COLOR = QColor(165, 165, 178)

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
        # When a recording start is known and real-time mode is on, times are
        # shown as wall-clock timestamps (with date) instead of file offsets.
        self._start_dt: datetime | None = None
        self._real_time = False
        # Global timeline mode: the track spans a whole day (in day-ms) and the
        # scan-range handles are suppressed; footage is drawn as a thin coverage
        # line instead of a played fill.
        self._global = False
        self._coverage: list[tuple[int, int]] = []
        self.setMinimumHeight(66)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        self._start_edit = self._make_editor(self._START_COLOR)
        self._end_edit = self._make_editor(self._END_COLOR)
        self._start_edit.editingFinished.connect(lambda: self._commit_editor("start"))
        self._end_edit.editingFinished.connect(lambda: self._commit_editor("end"))
        self._apply_editor_widths()

        # A read-only bubble that tracks the cursor and shows the time under it,
        # its tail pointing down at the track.
        self._hover_label = _HoverReadout(self)

    def _make_editor(self, accent: QColor) -> QLineEdit:
        edit = QLineEdit(self)
        edit.setVisible(False)
        edit.setAlignment(Qt.AlignmentFlag.AlignCenter)
        edit.setFixedHeight(self._LABEL_H)
        edit.setFixedWidth(94)
        edit.setStyleSheet(
            "QLineEdit { background: #1e1e22; color: #f0f0f0; border: 1px solid %s;"
            " border-radius: 3px; padding: 0 2px; font-size: 11px; }" % accent.name()
        )
        return edit

    def _apply_editor_widths(self) -> None:
        """Widen the editors to fit a full timestamp in real-time mode, and
        keep their input hints in sync with the accepted format."""
        if self._use_real():
            width = 168
            tip = "Type a real time (YYYY-MM-DD HH:MM:SS or just HH:MM:SS)."
        else:
            width = 94
            tip = "Type a time (SS, MM:SS, or HH:MM:SS)."
        for edit in (self._start_edit, self._end_edit):
            edit.setFixedWidth(width)
            edit.setToolTip(tip)

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

    def set_start_datetime(self, start: datetime | None) -> None:
        """Provide the real recording start (None disables real-time mode)."""
        self._start_dt = start
        if start is None:
            self._real_time = False
        self._apply_editor_widths()
        if self._active is not None:
            self._update_editor_text(self._active)
            self._reposition_editors()
        self.update()

    def set_time_mode(self, real_time: bool) -> None:
        """Switch the displayed times between file offsets and real clock time.

        Real-time mode only takes effect when a recording start is known.
        """
        self._real_time = bool(real_time) and self._start_dt is not None
        self._apply_editor_widths()
        if self._active is not None:
            self._update_editor_text(self._active)
            self._reposition_editors()
        self.update()

    def set_global_mode(self, enabled: bool) -> None:
        """Toggle the aggregate day timeline (no per-file scan-range handles)."""
        enabled = bool(enabled)
        if enabled == self._global:
            return
        self._global = enabled
        if enabled:
            self._drag = None
            self._set_active(None)  # tuck away the range editors
        self.update()

    def set_coverage(self, segments: list[tuple[int, int]]) -> None:
        """Set the day-ms spans where footage exists (global mode only)."""
        self._coverage = [(int(a), int(b)) for a, b in segments]
        self.update()

    def _use_real(self) -> bool:
        return self._real_time and self._start_dt is not None

    def _fmt(self, ms: int) -> str:
        """Format a millisecond offset per the current display mode."""
        if self._use_real():
            return ms_to_realtime(self._start_dt, ms)
        return ms_to_timecode(ms)

    # ---- range helpers ----------------------------------------------------

    def _eff_start(self) -> int:
        return self._range_start_ms or 0

    def _eff_end(self) -> int:
        return self._range_end_ms if self._range_end_ms is not None else self._duration_ms

    def _emit_range(self) -> None:
        self.rangeChanged.emit(self._eff_start(), self._eff_end())

    # ---- geometry helpers -------------------------------------------------

    def _track_rect(self) -> QRectF:
        # The track sits just below the label row; the tick ruler occupies the
        # space underneath it.
        margin = 8.0
        height = 14.0
        top = self._LABEL_H + 4.0
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

        # Played portion (file mode) or the thin footage-coverage line (global).
        if self._duration_ms > 0:
            if self._global:
                self._paint_coverage(painter, track)
            else:
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
        # (The aggregate day timeline has no per-file scan range.)
        if self._duration_ms > 0 and not self._global:
            self._paint_range(painter, track)

        # Timeline ruler (multi-level ticks + labels) beneath the track.
        if self._duration_ms > 0:
            self._paint_ticks(painter, track)

        # Playhead, extended down through the ruler.
        if self._duration_ms > 0:
            x = self._ms_to_x(self._position_ms)
            painter.setPen(self._PLAYHEAD_COLOR)
            painter.setBrush(self._PLAYHEAD_COLOR)
            bottom = track.bottom() + 3 + self._TICK_LEN["day"]
            painter.drawRect(QRectF(x - 1.0, track.top() - 4, 2.0, bottom - (track.top() - 4)))

    def _paint_coverage(self, painter: QPainter, track: QRectF) -> None:
        """Draw a thin line over the track wherever footage exists (global mode)."""
        if not self._coverage:
            return
        height = 3.0
        cy = track.center().y()
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(self._COVERAGE_COLOR)
        for start_ms, end_ms in self._coverage:
            x0 = self._ms_to_x(start_ms)
            x1 = self._ms_to_x(end_ms)
            painter.drawRect(QRectF(x0, cy - height / 2, max(2.0, x1 - x0), height))

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

        self._paint_handle(painter, start_x, self._START_COLOR, opening=True)
        self._paint_handle(painter, end_x, self._END_COLOR, opening=False)

    def _paint_handle(
        self, painter: QPainter, x: float, color: QColor, opening: bool
    ) -> None:
        """Draw the handle as a bracket: '[' for start, ']' for end (the arms
        point inward, toward the selected range)."""
        track = self._track_rect()
        top = track.top() - 5
        bottom = track.bottom() + 5
        arm = 6.0 if opening else -6.0

        pen = QPen(color)
        pen.setWidthF(3.0)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPolyline(QPolygonF([
            QPointF(x + arm, top),
            QPointF(x, top),
            QPointF(x, bottom),
            QPointF(x + arm, bottom),
        ]))

    # ---- timeline ruler ---------------------------------------------------

    def _boundaries(self, interval: int) -> list[tuple[int, datetime | None, int]]:
        """Enumerate tick boundaries for ``interval`` (ms) that fall in the clip.

        Returns ``(offset_ms, wall_time_or_None, align_value)`` tuples. In
        real-time mode boundaries align to the wall clock (midnight, the top of
        the hour, …); otherwise they align to the file's own zero. The
        ``align_value`` is the time from that origin and is what classifies a
        boundary's coarsest level.
        """
        duration = self._duration_ms
        out: list[tuple[int, datetime | None, int]] = []
        if duration <= 0 or interval <= 0:
            return out
        if self._use_real() and self._start_dt is not None:
            midnight = self._start_dt.replace(hour=0, minute=0, second=0, microsecond=0)
            base_off = int(round((self._start_dt - midnight).total_seconds() * 1000))
            align = math.ceil(base_off / interval) * interval
            offset = align - base_off
            while offset <= duration:
                wall = midnight + timedelta(milliseconds=align)
                out.append((offset, wall, align))
                align += interval
                offset += interval
        else:
            align = 0
            while align <= duration:
                out.append((align, None, align))
                align += interval
        return out

    @staticmethod
    def _classify(align: int) -> str:
        """The coarsest tick level a boundary at ``align`` belongs to."""
        if align % 86_400_000 == 0:
            return "day"
        if align % 3_600_000 == 0:
            return "hour"
        if align % 900_000 == 0:
            return "q"
        return "min"

    def _tick_label(self, level: str, wall: datetime | None, offset: int) -> str:
        if wall is not None:
            return wall.strftime("%b %d") if level == "day" else wall.strftime("%H:%M")
        total_min = offset // 60_000
        return f"{total_min // 60}:{total_min % 60:02d}"

    def _build_ticks(self, track: QRectF) -> list[dict]:
        """Compute the ticks to draw for the current clip, choosing which levels
        are dense enough to show and which ticks get a (non-overlapping) label."""
        duration = self._duration_ms
        if duration <= 0 or track.width() <= 0:
            return []
        px_per_ms = track.width() / duration
        visible = {
            name for name in self._TICK_LEVELS
            if self._TICK_INTERVAL[name] * px_per_ms >= self._TICK_MIN_PX[name]
        }
        if not visible:
            return []
        finest = min(self._TICK_INTERVAL[name] for name in visible)
        by_interval = {iv: name for name, iv in self._TICK_INTERVAL.items()}
        finest_name = by_interval[finest]

        ticks: list[dict] = []
        for offset, wall, align in self._boundaries(finest):
            if offset <= 0 or offset >= duration:  # the edges carry the handles
                continue
            level = self._classify(align)
            if level not in visible:
                level = finest_name
            ticks.append({
                "x": self._ms_to_x(offset),
                "level": level,
                "label": self._tick_label(level, wall, offset),
            })

        # Thin labels coarse-level-first, keeping each at least ~46px from the
        # last kept one. Round coarse labels (the hour, midnight) win the slot,
        # and dense finer labels fall away — so the ruler never crowds.
        font = QFont(self.font())
        font.setPixelSize(10)
        fm = QFontMetrics(font)
        placed: list[tuple[float, float]] = []
        for tick in sorted(ticks, key=lambda t: (self._TICK_ORDER[t["level"]], t["x"])):
            half = max(fm.horizontalAdvance(tick["label"]) / 2 + 4, 23.0)
            x0, x1 = tick["x"] - half, tick["x"] + half
            if any(not (x1 <= p0 or x0 >= p1) for p0, p1 in placed):
                tick["label"] = None
            else:
                placed.append((x0, x1))
        return ticks

    def _paint_ticks(self, painter: QPainter, track: QRectF) -> None:
        ticks = self._build_ticks(track)
        if not ticks:
            return
        y0 = track.bottom() + 3
        painter.save()
        for tick in ticks:
            level = tick["level"]
            pen = QPen(self._TICK_COLOR[level])
            pen.setWidthF(1.2 if level in ("day", "hour") else 1.0)
            painter.setPen(pen)
            x = tick["x"]
            painter.drawLine(QPointF(x, y0), QPointF(x, y0 + self._TICK_LEN[level]))

        font = QFont(self.font())
        font.setPixelSize(10)
        painter.setFont(font)
        painter.setPen(self._TICK_LABEL_COLOR)
        ly = y0 + self._TICK_LEN["day"] + 1
        for tick in ticks:
            if not tick["label"]:
                continue
            rect = QRectF(tick["x"] - 40, ly, 80, 12)
            if rect.left() < 0:
                rect.moveLeft(0)
            elif rect.right() > self.width():
                rect.moveRight(self.width())
            painter.drawText(
                rect,
                Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop,
                tick["label"],
            )
        painter.restore()

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
        self._editor(which).setText(self._fmt(ms))

    def _show_hover(self, x: float) -> None:
        """Float the read-out bubble above the cursor, tail pointing at the
        track at position ``x``."""
        if self._duration_ms <= 0:
            self._hover_label.setVisible(False)
            return
        self._hover_label.set_text(self._fmt(self._x_to_ms(x)))
        w = self._hover_label.width()
        h = self._hover_label.height()
        lx = max(0.0, min(x - w / 2, self.width() - w))
        y = max(0.0, self._track_rect().top() - 2 - h)
        self._hover_label.move(int(lx), int(y))
        self._hover_label.setVisible(True)
        self._hover_label.raise_()

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

    def _parse_time(self, text: str) -> int | None:
        """Parse the editor text into a millisecond offset.

        In real-time mode a wall-clock timestamp is accepted first; otherwise
        (or as a fallback) a relative file offset is parsed.
        """
        if self._use_real():
            ms = self._parse_realtime(text)
            if ms is not None:
                return ms
        return self._parse_relative(text)

    def _parse_realtime(self, text: str) -> int | None:
        """Parse a wall-clock timestamp into an offset from the recording start.

        Accepts a full ``YYYY-MM-DD HH:MM:SS(.mmm)`` or a time-only
        ``HH:MM(:SS(.mmm))`` value (assumed to be on the recording's day, or
        the next day if it would otherwise precede the start)."""
        token = text.strip()
        if not token or self._start_dt is None:
            return None
        for fmt, time_only in (
            ("%Y-%m-%d %H:%M:%S.%f", False),
            ("%Y-%m-%d %H:%M:%S", False),
            ("%Y-%m-%d %H:%M", False),
            ("%H:%M:%S.%f", True),
            ("%H:%M:%S", True),
            ("%H:%M", True),
        ):
            try:
                parsed = datetime.strptime(token, fmt)
            except ValueError:
                continue
            if time_only:
                parsed = self._start_dt.replace(
                    hour=parsed.hour, minute=parsed.minute,
                    second=parsed.second, microsecond=parsed.microsecond,
                )
                if parsed < self._start_dt:
                    parsed += timedelta(days=1)
            delta_ms = (parsed - self._start_dt).total_seconds() * 1000
            return max(0, int(round(delta_ms)))
        return None

    @staticmethod
    def _parse_relative(text: str) -> int | None:
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
        if self._duration_ms <= 0 or self._global:
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
            self._hover_label.setVisible(False)
            return
        if event.buttons() & Qt.MouseButton.LeftButton:
            self._seek_to_x(pos.x())
        if self._handle_at(pos.x(), pos.y()) is not None:
            # Over a handle: the bracket's own editor reads out the bound, so
            # keep the hover label out of the way.
            self.setCursor(Qt.CursorShape.SplitHCursor)
            self._hover_label.setVisible(False)
        else:
            self.setCursor(Qt.CursorShape.PointingHandCursor)
            self._show_hover(pos.x())

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if self._drag is not None:
            which = self._drag
            self._drag = None
            # Hand focus to the label so the bound can be typed precisely.
            editor = self._editor(which)
            editor.setFocus(Qt.FocusReason.MouseFocusReason)
            editor.selectAll()

    def leaveEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        self._hover_label.setVisible(False)
        super().leaveEvent(event)

    def resizeEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        super().resizeEvent(event)
        self._reposition_editors()
