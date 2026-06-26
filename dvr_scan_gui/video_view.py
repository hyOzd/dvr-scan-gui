"""Video display with an interactive detection-region editor.

The video is shown via a :class:`QGraphicsVideoItem` inside a graphics view so
that overlay items (the region outline) reliably composite on top of the video
regardless of the multimedia backend.

The video item is sized to the video's *native* resolution, so scene
coordinates are exactly video-pixel coordinates — the region can be reported to
DVR-Scan's ``-a X0 Y0 X1 Y1 …`` argument without any further mapping.

Two region shapes are supported:

* **rectangle** — press, drag, release.
* **polygon** — click to add each vertex; double-click, or click near the first
  vertex, to close. Right-click removes the last vertex; Esc cancels.

The region is drawn as a dashed outline with no fill. It can be deleted
entirely, or disabled (kept on screen, dimmed, but excluded from the scan).
"""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import (
    QBrush,
    QColor,
    QKeyEvent,
    QMouseEvent,
    QPainter,
    QPen,
    QPolygonF,
)
from PySide6.QtMultimediaWidgets import QGraphicsVideoItem
from PySide6.QtWidgets import (
    QGraphicsPolygonItem,
    QGraphicsScene,
    QGraphicsView,
)

MODE_RECTANGLE = "rectangle"
MODE_POLYGON = "polygon"


def _dashed_pen(color: QColor) -> QPen:
    pen = QPen(color, 2, Qt.PenStyle.DashLine)
    pen.setCosmetic(True)  # keep a constant 2px dash regardless of view scaling
    return pen


class VideoView(QGraphicsView):
    """A graphics-view based video display supporting one editable region.

    Signals:
        regionChanged(bool): emitted with whether a usable region now exists.
    """

    regionChanged = Signal(bool)

    _ACTIVE_PEN = _dashed_pen(QColor(255, 196, 0))
    _DISABLED_PEN = _dashed_pen(QColor(150, 150, 150))
    _MIN_SIZE = 8.0  # minimum rectangle edge length, in video pixels

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)
        self.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.setFrameShape(QGraphicsView.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setBackgroundBrush(QColor("black"))
        self.setMinimumSize(480, 300)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        self.video_item = QGraphicsVideoItem()
        self.video_item.setPos(0, 0)
        self._scene.addItem(self.video_item)
        self.video_item.nativeSizeChanged.connect(self._on_native_size_changed)

        self._region_item: QGraphicsPolygonItem | None = None
        self._region_kind: str | None = None  # of the *committed* region
        self._region_enabled = True
        self._mode = MODE_RECTANGLE
        self._edit_mode = False
        self._video_size = QRectF()  # 0,0,w,h in pixels once known

        # Rectangle drag / polygon construction state.
        self._rect_origin: QPointF | None = None
        self._poly_points: list[QPointF] = []

    # ---- configuration ----------------------------------------------------

    def set_mode(self, mode: str) -> None:
        if mode not in (MODE_RECTANGLE, MODE_POLYGON):
            return
        if mode != self._mode:
            self._mode = mode
            self._cancel_in_progress()
            self.clear_region()  # a half-defined shape of another kind is moot

    def set_edit_mode(self, enabled: bool) -> None:
        """Enable drawing of the detection region."""
        self._edit_mode = enabled and not self._video_size.isEmpty()
        if not self._edit_mode:
            self._cancel_in_progress()
        self.setCursor(
            Qt.CursorShape.CrossCursor if self._edit_mode else Qt.CursorShape.ArrowCursor
        )

    def set_region_enabled(self, enabled: bool) -> None:
        """Toggle whether the region applies to the scan (and dim it if not)."""
        self._region_enabled = enabled
        self._apply_pen()

    def is_region_enabled(self) -> bool:
        return self._region_enabled

    def can_edit(self) -> bool:
        """True once the video resolution is known (so pixel mapping works)."""
        return not self._video_size.isEmpty()

    # ---- region state -----------------------------------------------------

    def clear_region(self) -> None:
        self._cancel_in_progress()
        if self._region_item is not None:
            self._scene.removeItem(self._region_item)
            self._region_item = None
            self._region_kind = None
            self.regionChanged.emit(False)

    def has_region(self) -> bool:
        return self._region_item is not None

    def region_kind(self) -> str | None:
        return self._region_kind

    def region_points(self) -> list[tuple[int, int]] | None:
        """Return the committed region's vertices as integer (x, y) points."""
        if self._region_item is None:
            return None
        pts = [(round(p.x()), round(p.y())) for p in self._region_item.polygon()]
        return pts if len(pts) >= 3 else None

    # ---- video sizing -----------------------------------------------------

    def _on_native_size_changed(self, size) -> None:
        self.set_video_size(size)

    def set_video_size(self, size) -> None:
        """Set the source resolution (in pixels).

        Called both from the video item's ``nativeSizeChanged`` (once a frame is
        presented) and from the player's metadata (available earlier, and the
        only source under platforms that never present a frame). Idempotent.
        """
        if size is None or size.isEmpty():
            return
        new_size = QRectF(0, 0, size.width(), size.height())
        if new_size == self._video_size:
            return
        self._video_size = new_size
        self.video_item.setSize(size)
        self._scene.setSceneRect(self._video_size)
        # A region from a previous (differently-sized) video is no longer valid.
        self.clear_region()
        self._fit()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._fit()

    def _fit(self) -> None:
        if not self._video_size.isEmpty():
            self.fitInView(self._video_size, Qt.AspectRatioMode.KeepAspectRatio)

    # ---- drawing helpers --------------------------------------------------

    def _clamp_to_video(self, point: QPointF) -> QPointF:
        x = min(max(point.x(), 0.0), self._video_size.width())
        y = min(max(point.y(), 0.0), self._video_size.height())
        return QPointF(x, y)

    def _ensure_item(self) -> QGraphicsPolygonItem:
        if self._region_item is None:
            self._region_item = QGraphicsPolygonItem()
            self._region_item.setBrush(QBrush(Qt.BrushStyle.NoBrush))  # outline only
            self._scene.addItem(self._region_item)
        self._apply_pen()
        return self._region_item

    def _apply_pen(self) -> None:
        if self._region_item is not None:
            self._region_item.setPen(
                self._ACTIVE_PEN if self._region_enabled else self._DISABLED_PEN
            )

    def _scene_threshold(self, view_pixels: float) -> float:
        scale = self.transform().m11()
        return view_pixels / scale if scale else view_pixels

    def _cancel_in_progress(self) -> None:
        """Discard an unfinished rectangle drag or polygon."""
        was_building = self._rect_origin is not None or bool(self._poly_points)
        self._rect_origin = None
        self._poly_points = []
        if was_building and self._region_item is not None and self._region_kind is None:
            # Nothing was ever committed into this item — remove it.
            self._scene.removeItem(self._region_item)
            self._region_item = None

    def _commit(self, kind: str) -> None:
        self._region_kind = kind
        self.set_region_enabled(True)
        self.regionChanged.emit(True)

    # ---- mouse: rectangle -------------------------------------------------

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if self._edit_mode:
            point = self._clamp_to_video(self.mapToScene(event.pos()))
            if self._mode == MODE_RECTANGLE:
                self._rect_press(event, point)
                return
            if self._mode == MODE_POLYGON:
                self._poly_press(event, point)
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._edit_mode:
            point = self._clamp_to_video(self.mapToScene(event.pos()))
            if self._mode == MODE_RECTANGLE and self._rect_origin is not None:
                self._rect_to(point)
                event.accept()
                return
            if self._mode == MODE_POLYGON and self._poly_points:
                self._poly_preview(point)
                event.accept()
                return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if self._edit_mode and self._mode == MODE_RECTANGLE and self._rect_origin:
            self._rect_finish()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        if self._edit_mode and self._mode == MODE_POLYGON:
            self._poly_close()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if self._edit_mode and self._mode == MODE_POLYGON and self._poly_points:
            if event.key() == Qt.Key.Key_Escape:
                self._cancel_in_progress()
                self.regionChanged.emit(self.has_region())
                return
            if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                self._poly_close()
                return
        super().keyPressEvent(event)

    # ---- rectangle construction ------------------------------------------

    def _rect_press(self, event: QMouseEvent, point: QPointF) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            return
        # Starting a fresh rectangle replaces any existing region.
        self.clear_region()
        self._rect_origin = point
        item = self._ensure_item()
        item.setPolygon(QPolygonF([point]))
        event.accept()

    def _rect_to(self, point: QPointF) -> None:
        rect = QRectF(self._rect_origin, point).normalized()
        corners = [
            rect.topLeft(),
            rect.topRight(),
            rect.bottomRight(),
            rect.bottomLeft(),
        ]
        self._region_item.setPolygon(QPolygonF(corners))

    def _rect_finish(self) -> None:
        self._rect_origin = None
        rect = self._region_item.polygon().boundingRect()
        if rect.width() < self._MIN_SIZE or rect.height() < self._MIN_SIZE:
            self.clear_region()  # discard accidental tiny drags
        else:
            self._commit(MODE_RECTANGLE)

    # ---- polygon construction --------------------------------------------

    def _poly_press(self, event: QMouseEvent, point: QPointF) -> None:
        if event.button() == Qt.MouseButton.RightButton:
            if self._poly_points:
                self._poly_points.pop()
                self._poly_preview(point)
            return
        if event.button() != Qt.MouseButton.LeftButton:
            return

        if not self._poly_points:
            # Starting a new polygon replaces any existing committed region.
            self.clear_region()
            self._ensure_item()

        threshold = self._scene_threshold(12.0)
        # Click near the first vertex closes the polygon.
        if len(self._poly_points) >= 3:
            first = self._poly_points[0]
            if (point - first).manhattanLength() <= threshold:
                self._poly_close()
                return
        # Ignore points coincident with the previous one (e.g. double-click).
        if self._poly_points and (
            point - self._poly_points[-1]
        ).manhattanLength() <= threshold:
            return

        self._poly_points.append(point)
        self._poly_preview(point)
        event.accept()

    def _poly_preview(self, cursor: QPointF) -> None:
        if self._region_item is None:
            return
        self._region_item.setPolygon(QPolygonF(self._poly_points + [cursor]))

    def _poly_close(self) -> None:
        if len(self._poly_points) < 3:
            return
        self._region_item.setPolygon(QPolygonF(self._poly_points))
        self._poly_points = []
        self._commit(MODE_POLYGON)
