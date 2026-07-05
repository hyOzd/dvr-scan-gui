"""Video display with an interactive, multi-region detection-region editor.

The video is shown via a :class:`QGraphicsVideoItem` inside a graphics view so
overlay items (the region outlines) reliably composite on top of the video
regardless of the multimedia backend. The video item is sized to the video's
*native* resolution, so scene coordinates are exactly video-pixel coordinates —
each region maps to one of DVR-Scan's ``-a X0 Y0 X1 Y1 …`` arguments with no
further conversion.

The editor is tool-based, like a simple drawing program:

* **pointer** (default) — drag the corner handles of any region to adjust it.
* **rectangle** — click two opposite corners; the box previews while you move.
* **polygon** — click each vertex (the next edge previews live); double-click,
  or click the first vertex, to close. Right-click removes the last vertex.
* **delete** — click a region to remove it.

Finishing a drawing, or pressing Esc, returns to the pointer tool. Regions are
drawn as dashed outlines with no fill, and the whole set can be enabled or
disabled (kept on screen but excluded from the scan).
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
    QGraphicsItem,
    QGraphicsPolygonItem,
    QGraphicsRectItem,
    QGraphicsScene,
    QGraphicsView,
    QLabel,
    QMenu,
)

TOOL_POINTER = "pointer"
TOOL_RECTANGLE = "rectangle"
TOOL_POLYGON = "polygon"
TOOL_DELETE = "delete"

_KIND_KEY = 0  # QGraphicsItem data key storing a region's creation kind


def _dashed_pen(color: QColor) -> QPen:
    pen = QPen(color, 2, Qt.PenStyle.DashLine)
    pen.setCosmetic(True)  # constant 2px dash regardless of view scaling
    return pen


class VideoView(QGraphicsView):
    """Graphics-view video display supporting several editable regions.

    Signals:
        regionsChanged(): a region was added, edited, or removed.
        toolReset():       the active tool reverted to the pointer (so the
                           owning toolbar can re-check the pointer button).
    """

    regionsChanged = Signal()
    toolReset = Signal()

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
        self.setMouseTracking(True)

        self.video_item = QGraphicsVideoItem()
        self.video_item.setPos(0, 0)
        self._scene.addItem(self.video_item)
        self.video_item.nativeSizeChanged.connect(self._on_native_size_changed)

        # Playback-speed indicator, pinned to the top-right of the viewport. It
        # is a plain child widget (not a scene item) so it stays a fixed size
        # regardless of the video's scaling, and it ignores mouse events so it
        # never interferes with region editing underneath it.
        self._speed_text = ""
        self._speed_overlay_enabled = True
        self._speed_overlay = QLabel(self)
        self._speed_overlay.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents
        )
        self._speed_overlay.setStyleSheet(
            "background-color: rgba(0, 0, 0, 160); color: white;"
            " padding: 2px 8px; border-radius: 4px; font-weight: bold;"
        )
        self._speed_overlay.hide()

        self._regions: list[QGraphicsPolygonItem] = []
        self._handles: list[QGraphicsRectItem] = []
        self._region_enabled = True
        self._tool = TOOL_POINTER
        self._video_size = QRectF()  # 0,0,w,h once known

        # In-progress drawing / editing state.
        self._draft_item: QGraphicsPolygonItem | None = None
        self._rect_first: QPointF | None = None
        self._poly_points: list[QPointF] = []
        self._drag_item: QGraphicsPolygonItem | None = None
        self._drag_vertex = -1

    # ---- configuration ----------------------------------------------------

    def set_tool(self, tool: str) -> None:
        if tool not in (TOOL_POINTER, TOOL_RECTANGLE, TOOL_POLYGON, TOOL_DELETE):
            return
        self._cancel_draft()
        self._tool = tool
        self._update_cursor()
        self._refresh_handles()

    def tool(self) -> str:
        return self._tool

    def set_region_enabled(self, enabled: bool) -> None:
        """Toggle whether the regions apply to the scan (and dim them if not)."""
        self._region_enabled = enabled
        self._apply_pens()

    def is_region_enabled(self) -> bool:
        return self._region_enabled

    def can_edit(self) -> bool:
        """True once the video resolution is known (so pixel mapping works)."""
        return not self._video_size.isEmpty()

    # ---- playback-speed overlay -------------------------------------------

    def set_speed_text(self, text: str) -> None:
        """Set the speed shown in the corner overlay (e.g. ``"2x"``).

        An empty string means normal speed, which hides the overlay entirely.
        """
        self._speed_text = text
        self._update_speed_overlay()

    def is_speed_overlay_enabled(self) -> bool:
        return self._speed_overlay_enabled

    def set_speed_overlay_enabled(self, enabled: bool) -> None:
        """User preference for whether the speed overlay may appear at all."""
        self._speed_overlay_enabled = enabled
        self._update_speed_overlay()

    def _update_speed_overlay(self) -> None:
        if self._speed_overlay_enabled and self._speed_text:
            self._speed_overlay.setText(self._speed_text)
            self._speed_overlay.adjustSize()
            self._position_speed_overlay()
            self._speed_overlay.show()
        else:
            self._speed_overlay.hide()

    def _position_speed_overlay(self) -> None:
        margin = 8
        size = self._speed_overlay.size()
        self._speed_overlay.move(
            self.width() - size.width() - margin, margin
        )

    # ---- region state -----------------------------------------------------

    def has_regions(self) -> bool:
        return bool(self._regions)

    def region_count(self) -> int:
        return len(self._regions)

    def region_kinds(self) -> list[str]:
        return [item.data(_KIND_KEY) for item in self._regions]

    def regions_points(self) -> list[list[tuple[int, int]]]:
        """Each region as a list of integer (x, y) vertices (≥3 points only)."""
        result = []
        for item in self._regions:
            pts = [(round(p.x()), round(p.y())) for p in item.polygon()]
            if len(pts) >= 3:
                result.append(pts)
        return result

    def set_regions(self, regions: list[list[tuple[int, int]]]) -> None:
        """Replace the displayed regions with ``regions`` (per-file restore).

        This is a *programmatic* change — it does not emit ``regionsChanged``.
        Points are clamped to the current video size; if the size is not yet
        known, the view is simply cleared (the caller re-applies once it is).
        """
        self._clear_regions_silent()
        if not self.can_edit():
            return
        for pts in regions:
            if len(pts) < 3:
                continue
            item = self._new_polygon_item(TOOL_POLYGON)
            item.setPolygon(
                QPolygonF([self._clamp(QPointF(float(x), float(y))) for x, y in pts])
            )
            self._regions.append(item)
        self._refresh_handles()

    # ---- video sizing -----------------------------------------------------

    def _on_native_size_changed(self, size) -> None:
        self.set_video_size(size)

    def set_video_size(self, size) -> None:
        """Set the source resolution (in pixels). Idempotent.

        Fed both from the video item's ``nativeSizeChanged`` (once a frame is
        presented) and from the player's metadata (available earlier, and the
        only source on platforms that never present a frame).
        """
        if size is None or size.isEmpty():
            return
        new_size = QRectF(0, 0, size.width(), size.height())
        if new_size == self._video_size:
            return
        self._video_size = new_size
        self.video_item.setSize(size)
        self._scene.setSceneRect(self._video_size)
        # Regions from a previous (differently-sized) video are no longer valid.
        # This is a programmatic reset (the owner restores the new file's
        # regions afterwards), so it must not emit regionsChanged.
        self._clear_regions_silent()
        self._fit()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._fit()
        self._position_speed_overlay()

    def _fit(self) -> None:
        if not self._video_size.isEmpty():
            self.fitInView(self._video_size, Qt.AspectRatioMode.KeepAspectRatio)

    # ---- helpers ----------------------------------------------------------

    def _clamp(self, point: QPointF) -> QPointF:
        x = min(max(point.x(), 0.0), self._video_size.width())
        y = min(max(point.y(), 0.0), self._video_size.height())
        return QPointF(x, y)

    def _scene_threshold(self, view_pixels: float) -> float:
        scale = self.transform().m11()
        return view_pixels / scale if scale else view_pixels

    def _pen(self) -> QPen:
        return self._ACTIVE_PEN if self._region_enabled else self._DISABLED_PEN

    def _apply_pens(self) -> None:
        for item in self._regions:
            item.setPen(self._pen())
        if self._draft_item is not None:
            self._draft_item.setPen(self._pen())

    def _new_polygon_item(self, kind: str) -> QGraphicsPolygonItem:
        item = QGraphicsPolygonItem()
        item.setBrush(QBrush(Qt.BrushStyle.NoBrush))  # outline only
        item.setPen(self._pen())
        item.setZValue(1)
        item.setData(_KIND_KEY, kind)
        self._scene.addItem(item)
        return item

    def _update_cursor(self) -> None:
        cursors = {
            TOOL_POINTER: Qt.CursorShape.ArrowCursor,
            TOOL_RECTANGLE: Qt.CursorShape.CrossCursor,
            TOOL_POLYGON: Qt.CursorShape.CrossCursor,
            TOOL_DELETE: Qt.CursorShape.PointingHandCursor,
        }
        self.setCursor(cursors.get(self._tool, Qt.CursorShape.ArrowCursor))

    def _cancel_draft(self) -> None:
        if self._draft_item is not None:
            self._scene.removeItem(self._draft_item)
            self._draft_item = None
        self._rect_first = None
        self._poly_points = []
        self._drag_item = None
        self._drag_vertex = -1

    def _reset_to_pointer(self) -> None:
        self._cancel_draft()
        self._tool = TOOL_POINTER
        self._update_cursor()
        self._refresh_handles()
        self.toolReset.emit()

    def _commit_region(self) -> None:
        self._regions.append(self._draft_item)
        self._draft_item = None
        self.regionsChanged.emit()
        self._reset_to_pointer()

    def _clear_regions_silent(self) -> None:
        """Remove every region without emitting regionsChanged (programmatic)."""
        for item in self._regions:
            self._scene.removeItem(item)
        self._regions = []
        self._cancel_draft()
        self._refresh_handles()

    def clear_regions(self) -> None:
        """Remove every region as a user action (emits regionsChanged)."""
        self._clear_regions_silent()
        self.regionsChanged.emit()

    # ---- vertex handles (pointer tool) ------------------------------------

    def _refresh_handles(self) -> None:
        for handle in self._handles:
            self._scene.removeItem(handle)
        self._handles = []
        if self._tool != TOOL_POINTER:
            return
        for item in self._regions:
            poly = item.polygon()
            for i in range(poly.count()):
                handle = QGraphicsRectItem(-4, -4, 8, 8)
                handle.setFlag(
                    QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations
                )
                handle.setPen(QPen(QColor(30, 30, 30)))
                handle.setBrush(QBrush(QColor(255, 196, 0)))
                handle.setZValue(2)
                handle.setPos(poly.at(i))
                self._scene.addItem(handle)
                self._handles.append(handle)

    # ---- hit testing ------------------------------------------------------

    def _find_vertex(self, point: QPointF):
        threshold = self._scene_threshold(10.0)
        best = None
        best_dist = threshold
        for item in self._regions:
            poly = item.polygon()
            for i in range(poly.count()):
                dist = (poly.at(i) - point).manhattanLength()
                if dist <= best_dist:
                    best_dist = dist
                    best = (item, i)
        return best

    def _find_region(self, point: QPointF) -> QGraphicsPolygonItem | None:
        threshold = self._scene_threshold(8.0)
        for item in reversed(self._regions):  # topmost first
            poly = item.polygon()
            if poly.containsPoint(point, Qt.FillRule.OddEvenFill):
                return item
            for i in range(poly.count()):
                if (poly.at(i) - point).manhattanLength() <= threshold:
                    return item
        return None

    @staticmethod
    def _set_vertex(item: QGraphicsPolygonItem, index: int, point: QPointF) -> None:
        pts = list(item.polygon())
        pts[index] = point
        item.setPolygon(QPolygonF(pts))

    # ---- mouse dispatch ---------------------------------------------------

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if not self._video_size.isEmpty():
            point = self._clamp(self.mapToScene(event.pos()))
            if self._tool == TOOL_RECTANGLE:
                return self._rect_press(event, point)
            if self._tool == TOOL_POLYGON:
                return self._poly_press(event, point)
            if self._tool == TOOL_DELETE:
                return self._delete_press(event, point)
            if self._tool == TOOL_POINTER:
                return self._pointer_press(event, point)
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if not self._video_size.isEmpty():
            point = self._clamp(self.mapToScene(event.pos()))
            if self._tool == TOOL_RECTANGLE and self._rect_first is not None:
                self._rect_preview(point)
                return event.accept()
            if self._tool == TOOL_POLYGON and self._poly_points:
                self._poly_preview(point)
                return event.accept()
            if self._tool == TOOL_POINTER:
                self._pointer_move(point)
                return event.accept()
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if self._tool == TOOL_POINTER and self._drag_item is not None:
            self._drag_item = None
            self._drag_vertex = -1
            self.regionsChanged.emit()
            return event.accept()
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        if self._tool == TOOL_POLYGON:
            self._poly_close()
            return event.accept()
        super().mouseDoubleClickEvent(event)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self._reset_to_pointer()
            return
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if self._tool == TOOL_POLYGON:
                self._poly_close()
                return
        super().keyPressEvent(event)

    def contextMenuEvent(self, event) -> None:
        # The polygon tool already uses right-click to drop the last vertex, so
        # don't hijack it with a menu there.
        if self._tool == TOOL_POLYGON:
            return
        menu = QMenu(self)
        action = menu.addAction("Show speed indicator")
        action.setCheckable(True)
        action.setChecked(self._speed_overlay_enabled)
        action.toggled.connect(self.set_speed_overlay_enabled)
        menu.exec(event.globalPos())
        event.accept()

    # ---- rectangle tool ---------------------------------------------------

    def _rect_press(self, event: QMouseEvent, point: QPointF) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            return
        if self._rect_first is None:
            self._rect_first = point
            self._draft_item = self._new_polygon_item(TOOL_RECTANGLE)
            self._draft_item.setPolygon(QPolygonF([point]))
        else:
            self._rect_preview(point)
            rect = self._draft_item.polygon().boundingRect()
            if rect.width() < self._MIN_SIZE or rect.height() < self._MIN_SIZE:
                self._cancel_draft()  # degenerate; discard and stay in the tool
            else:
                self._commit_region()
        event.accept()

    def _rect_preview(self, point: QPointF) -> None:
        rect = QRectF(self._rect_first, point).normalized()
        self._draft_item.setPolygon(
            QPolygonF(
                [rect.topLeft(), rect.topRight(), rect.bottomRight(), rect.bottomLeft()]
            )
        )

    # ---- polygon tool -----------------------------------------------------

    def _poly_press(self, event: QMouseEvent, point: QPointF) -> None:
        if event.button() == Qt.MouseButton.RightButton:
            if self._poly_points:
                self._poly_points.pop()
                if self._poly_points:
                    self._poly_preview(point)
                else:
                    self._cancel_draft()
            return
        if event.button() != Qt.MouseButton.LeftButton:
            return

        if not self._poly_points:
            self._draft_item = self._new_polygon_item(TOOL_POLYGON)

        threshold = self._scene_threshold(12.0)
        if len(self._poly_points) >= 3:
            if (point - self._poly_points[0]).manhattanLength() <= threshold:
                self._poly_close()
                return
        if self._poly_points and (
            point - self._poly_points[-1]
        ).manhattanLength() <= threshold:
            return  # ignore points coincident with the previous one

        self._poly_points.append(point)
        self._poly_preview(point)
        event.accept()

    def _poly_preview(self, cursor: QPointF) -> None:
        if self._draft_item is not None:
            self._draft_item.setPolygon(QPolygonF(self._poly_points + [cursor]))

    def _poly_close(self) -> None:
        if len(self._poly_points) < 3:
            return
        self._draft_item.setPolygon(QPolygonF(self._poly_points))
        self._poly_points = []
        self._commit_region()

    # ---- delete tool ------------------------------------------------------

    def _delete_press(self, event: QMouseEvent, point: QPointF) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            return
        item = self._find_region(point)
        if item is not None:
            self._regions.remove(item)
            self._scene.removeItem(item)
            self._refresh_handles()
            self.regionsChanged.emit()
        event.accept()

    # ---- pointer tool -----------------------------------------------------

    def _pointer_press(self, event: QMouseEvent, point: QPointF) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            return
        found = self._find_vertex(point)
        if found is None:
            return
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self._delete_vertex(*found)  # Ctrl+click removes the vertex
        else:
            self._drag_item, self._drag_vertex = found
        event.accept()

    def _delete_vertex(self, item: QGraphicsPolygonItem, index: int) -> None:
        pts = list(item.polygon())
        if len(pts) <= 3:
            # A polygon needs ≥3 vertices, so removing one leaves no valid
            # region — drop the whole thing.
            self._regions.remove(item)
            self._scene.removeItem(item)
        else:
            del pts[index]
            item.setPolygon(QPolygonF(pts))
        self._refresh_handles()
        self.regionsChanged.emit()

    def _pointer_move(self, point: QPointF) -> None:
        if self._drag_item is not None:
            self._set_vertex(self._drag_item, self._drag_vertex, point)
            self._refresh_handles()
            return
        # Hover feedback: a move cursor when over a draggable vertex.
        over_vertex = self._find_vertex(point) is not None
        self.setCursor(
            Qt.CursorShape.SizeAllCursor
            if over_vertex
            else Qt.CursorShape.ArrowCursor
        )
