from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPen
from PySide6.QtMultimedia import QVideoSink
from PySide6.QtWidgets import QWidget

from .video_utils import Crop, preview_crop


class Preview(QWidget):
    crop_changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(320, 200)
        self.setMouseTracking(True)
        self.sink = QVideoSink(self)
        self.sink.videoFrameChanged.connect(self._frame)
        self.image = QImage()
        self.source_width, self.source_height = 1920, 1080
        self.crop = QRectF(0, 0, 1, 1)
        self.drag = None
        self.editable = True
        self.cropped = False
        self.setToolTip('Drag inside the selection to move it. Drag an edge or corner to resize.')

    def _frame(self, frame):
        if frame.isValid():
            self.image = frame.toImage()
            self.update()

    def set_source(self, width, height):
        self.source_width, self.source_height = width, height
        self.image = QImage()
        self.reset_crop()

    def reset_crop(self):
        self.crop = QRectF(0, 0, 1, 1)
        self.crop_changed.emit()
        self.update()

    def viewport(self):
        scale = min(self.width() / self.source_width, self.height() / self.source_height)
        w, h = self.source_width * scale, self.source_height * scale
        return QRectF((self.width() - w) / 2, (self.height() - h) / 2, w, h)

    def selection(self):
        v, c = self.viewport(), self.crop
        return QRectF(v.x() + c.x() * v.width(), v.y() + c.y() * v.height(),
                      c.width() * v.width(), c.height() * v.height())

    def source_crop(self) -> Crop:
        r, v = self.selection(), self.viewport()
        return preview_crop((r.x(), r.y(), r.width(), r.height()),
                            (v.x(), v.y(), v.width(), v.height()), self.source_width, self.source_height)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), Qt.GlobalColor.black)
        if self.cropped:
            crop = self.source_crop()
            scale = min(self.width() / crop.width, self.height() / crop.height)
            width, height = crop.width * scale, crop.height * scale
            target = QRectF((self.width() - width) / 2, (self.height() - height) / 2, width, height)
            if not self.image.isNull():
                sx, sy = self.image.width() / self.source_width, self.image.height() / self.source_height
                painter.drawImage(target, self.image, QRectF(crop.x * sx, crop.y * sy, crop.width * sx, crop.height * sy))
            return
        view, rect = self.viewport(), self.selection()
        if not self.image.isNull():
            painter.drawImage(view, self.image)
        if not self.editable:
            return
        shade = QColor(0, 0, 0, 105)
        painter.fillRect(QRectF(view.left(), view.top(), view.width(), rect.top() - view.top()), shade)
        painter.fillRect(QRectF(view.left(), rect.bottom(), view.width(), view.bottom() - rect.bottom()), shade)
        painter.fillRect(QRectF(view.left(), rect.top(), rect.left() - view.left(), rect.height()), shade)
        painter.fillRect(QRectF(rect.right(), rect.top(), view.right() - rect.right(), rect.height()), shade)
        painter.setPen(QPen(QColor('#557E5C'), 1))
        painter.drawRect(rect.adjusted(1, 1, -1, -1))
        for point in self.handles():
            painter.fillRect(QRectF(point.x() - 3, point.y() - 3, 6, 6), QColor('#557E5C'))

    def handles(self):
        r = self.selection()
        return [r.topLeft(), QPointF(r.center().x(), r.top()), r.topRight(),
                QPointF(r.right(), r.center().y()), r.bottomRight(),
                QPointF(r.center().x(), r.bottom()), r.bottomLeft(), QPointF(r.left(), r.center().y())]

    def hit(self, point):
        for i, handle in enumerate(self.handles()):
            if (point - handle).manhattanLength() <= 12:
                return i
        return 8 if self.selection().contains(point) else None

    def mousePressEvent(self, event):
        if self.editable and event.button() == Qt.MouseButton.LeftButton:
            hit = self.hit(event.position())
            if hit is not None:
                self.drag = (hit, event.position(), QRectF(self.crop))

    def mouseMoveEvent(self, event):
        if not self.editable:
            return
        if self.drag is None:
            hit = self.hit(event.position())
            cursors = {0: Qt.CursorShape.SizeFDiagCursor, 4: Qt.CursorShape.SizeFDiagCursor,
                       2: Qt.CursorShape.SizeBDiagCursor, 6: Qt.CursorShape.SizeBDiagCursor,
                       1: Qt.CursorShape.SizeVerCursor, 5: Qt.CursorShape.SizeVerCursor,
                       3: Qt.CursorShape.SizeHorCursor, 7: Qt.CursorShape.SizeHorCursor,
                       8: Qt.CursorShape.SizeAllCursor}
            self.setCursor(cursors.get(hit, Qt.CursorShape.ArrowCursor))
            return
        handle, origin, original = self.drag
        v = self.viewport()
        delta = event.position() - origin
        dx, dy = delta.x() / v.width(), delta.y() / v.height()
        c = QRectF(original)
        minw, minh = max(2 / self.source_width, 12 / v.width()), max(2 / self.source_height, 12 / v.height())
        if handle == 8:
            c.moveLeft(max(0, min(1 - c.width(), original.left() + dx)))
            c.moveTop(max(0, min(1 - c.height(), original.top() + dy)))
        else:
            if handle in (0, 6, 7):
                c.setLeft(max(0, min(original.right() - minw, original.left() + dx)))
            if handle in (2, 3, 4):
                c.setRight(min(1, max(original.left() + minw, original.right() + dx)))
            if handle in (0, 1, 2):
                c.setTop(max(0, min(original.bottom() - minh, original.top() + dy)))
            if handle in (4, 5, 6):
                c.setBottom(min(1, max(original.top() + minh, original.bottom() + dy)))
        self.crop = c
        self.crop_changed.emit()
        self.update()

    def mouseReleaseEvent(self, event):
        self.drag = None

