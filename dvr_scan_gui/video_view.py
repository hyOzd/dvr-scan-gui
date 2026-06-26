"""Video display with an interactive rectangular detection-region editor.

The video is shown via a :class:`QGraphicsVideoItem` inside a graphics view so
that overlay items (the region rectangle and its handles) reliably composite on
top of the video regardless of the multimedia backend.

The video item is sized to the video's *native* resolution, so scene
coordinates are exactly video-pixel coordinates — the region can be reported to
DVR-Scan's ``-a X0 Y0 X1 Y1 …`` argument without any further mapping.
"""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QMouseEvent, QPainter, QPen
from PySide6.QtMultimediaWidgets import QGraphicsVideoItem
from PySide6.QtWidgets import (
    QGraphicsRectItem,
    QGraphicsScene,
    QGraphicsView,
)


class VideoView(QGraphicsView):
    """A graphics-view based video display that supports drawing one region.

    Signals:
        regionChanged(bool): emitted with whether a usable region now exists.
    """

    regionChanged = Signal(bool)

    _REGION_PEN = QPen(QColor(255, 196, 0), 2)
    _REGION_BRUSH = QBrush(QColor(255, 196, 0, 50))
    _MIN_SIZE = 8.0  # minimum region edge length, in video pixels

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

        self.video_item = QGraphicsVideoItem()
        self.video_item.setPos(0, 0)
        self._scene.addItem(self.video_item)
        self.video_item.nativeSizeChanged.connect(self._on_native_size_changed)

        self._region_item: QGraphicsRectItem | None = None
        self._edit_mode = False
        self._drag_origin: QPointF | None = None
        self._video_size = QRectF()  # 0,0,w,h in pixels once known

    # ---- public API -------------------------------------------------------

    def set_edit_mode(self, enabled: bool) -> None:
        """Enable click-drag drawing of the detection region."""
        self._edit_mode = enabled and not self._video_size.isEmpty()
        self.setCursor(
            Qt.CursorShape.CrossCursor if self._edit_mode else Qt.CursorShape.ArrowCursor
        )

    def can_edit(self) -> bool:
        """True once the video resolution is known (so pixel mapping works)."""
        return not self._video_size.isEmpty()

    def clear_region(self) -> None:
        if self._region_item is not None:
            self._scene.removeItem(self._region_item)
            self._region_item = None
            self.regionChanged.emit(False)

    def has_region(self) -> bool:
        return self._region_item is not None

    def region_points(self) -> list[tuple[int, int]] | None:
        """Return the region as 4 integer (x, y) corner points, or None."""
        if self._region_item is None:
            return None
        r = self._region_item.rect().normalized()
        return [
            (round(r.left()), round(r.top())),
            (round(r.right()), round(r.top())),
            (round(r.right()), round(r.bottom())),
            (round(r.left()), round(r.bottom())),
        ]

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

    # ---- region drawing ---------------------------------------------------

    def _clamp_to_video(self, point: QPointF) -> QPointF:
        x = min(max(point.x(), 0.0), self._video_size.width())
        y = min(max(point.y(), 0.0), self._video_size.height())
        return QPointF(x, y)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if self._edit_mode and event.button() == Qt.MouseButton.LeftButton:
            self._drag_origin = self._clamp_to_video(self.mapToScene(event.pos()))
            if self._region_item is None:
                self._region_item = QGraphicsRectItem()
                self._region_item.setPen(self._REGION_PEN)
                self._region_item.setBrush(self._REGION_BRUSH)
                self._scene.addItem(self._region_item)
            self._region_item.setRect(QRectF(self._drag_origin, self._drag_origin))
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._edit_mode and self._drag_origin is not None:
            current = self._clamp_to_video(self.mapToScene(event.pos()))
            self._region_item.setRect(QRectF(self._drag_origin, current).normalized())
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if self._edit_mode and self._drag_origin is not None:
            self._drag_origin = None
            rect = self._region_item.rect().normalized()
            if rect.width() < self._MIN_SIZE or rect.height() < self._MIN_SIZE:
                self.clear_region()  # discard accidental tiny drags
            else:
                self.regionChanged.emit(True)
            event.accept()
            return
        super().mouseReleaseEvent(event)
