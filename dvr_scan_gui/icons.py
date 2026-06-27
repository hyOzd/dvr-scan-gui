"""Programmatically drawn icons for the region tool buttons.

Drawing the icons (rather than shipping image files) keeps the package
self-contained and lets the glyphs pick up the current palette colour so they
stay legible on both light and dark themes.
"""

from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QBrush,
    QColor,
    QIcon,
    QPainter,
    QPen,
    QPixmap,
    QPolygonF,
)

_GRID = 32.0  # all shapes are drawn in a 0..32 coordinate space


def _draw_pointer(p: QPainter, color: QColor) -> None:
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(color))
    pts = [(8, 5), (8, 26), (13, 21), (17, 29), (20, 28), (16, 20), (24, 20)]
    p.drawPolygon(QPolygonF([QPointF(x, y) for x, y in pts]))


def _draw_rectangle(p: QPainter, color: QColor) -> None:
    p.setPen(QPen(color, 2.4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap,
                  Qt.PenJoinStyle.RoundJoin))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawRect(QRectF(7, 9, 18, 14))


def _draw_polygon(p: QPainter, color: QColor) -> None:
    p.setPen(QPen(color, 2.4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap,
                  Qt.PenJoinStyle.RoundJoin))
    p.setBrush(Qt.BrushStyle.NoBrush)
    cx, cy, r = 16.0, 16.5, 11.0
    pts = []
    for i in range(5):  # a pentagon, point-up
        angle = -math.pi / 2 + i * 2 * math.pi / 5
        pts.append(QPointF(cx + r * math.cos(angle), cy + r * math.sin(angle)))
    p.drawPolygon(QPolygonF(pts))


def _draw_delete(p: QPainter, color: QColor) -> None:
    pen = QPen(color, 2.2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap,
               Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawLine(QPointF(13, 6), QPointF(19, 6))          # handle
    p.drawLine(QPointF(8, 9), QPointF(24, 9))           # lid
    # tapered body
    p.drawPolyline(QPolygonF([
        QPointF(10, 9), QPointF(11, 26), QPointF(21, 26), QPointF(22, 9),
    ]))
    thin = QPen(color, 1.6, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
    p.setPen(thin)
    for x in (14, 16, 18):                              # vertical ribs
        p.drawLine(QPointF(x, 12), QPointF(x, 23))


def _draw_clock(p: QPainter, color: QColor) -> None:
    pen = QPen(color, 2.2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap,
               Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawEllipse(QRectF(5, 6, 22, 22))                 # face
    p.drawLine(QPointF(16, 17), QPointF(16, 11))        # minute hand
    p.drawLine(QPointF(16, 17), QPointF(21, 19))        # hour hand


_DRAWERS = {
    "pointer": _draw_pointer,
    "rectangle": _draw_rectangle,
    "polygon": _draw_polygon,
    "delete": _draw_delete,
    "clock": _draw_clock,
}


def tool_icon(name: str, color: QColor, size: int = 64) -> QIcon:
    """Return a :class:`QIcon` for a region tool, drawn in ``color``."""
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.scale(size / _GRID, size / _GRID)
    drawer = _DRAWERS.get(name)
    if drawer is not None:
        drawer(painter, color)
    painter.end()
    return QIcon(pixmap)
