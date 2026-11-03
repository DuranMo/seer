"""Short, non-interactive notifications; no window or timer survives dismissal."""
from __future__ import annotations

from PySide6.QtCore import QPropertyAnimation, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QCursor, QGuiApplication, QPalette
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget


class NotificationPopup(QWidget):
    dismissed = Signal()
    FADE_MS = 150
    HOLD_MS = 1800
    ERROR_HOLD_MS = 5000

    def __init__(self, parent=None):
        super().__init__(parent, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.WindowDoesNotAcceptFocus
            | Qt.WindowType.WindowTransparentForInput)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setWindowTitle('Seer notification')
        palette = self.palette()
        palette.setColor(QPalette.ColorRole.Window, QColor('black'))
        palette.setColor(QPalette.ColorRole.WindowText, QColor('white'))
        self.setPalette(palette)
        self.setAutoFillBackground(True)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(4)
        self.title = QLabel('Seer')
        font = self.title.font()
        font.setPointSize(9)
        font.setBold(True)
        self.title.setFont(font)
        self.message = QLabel()
        self.message.setWordWrap(True)
        font = self.message.font()
        font.setPointSize(11)
        self.message.setFont(font)
        self.detail = QLabel()
        self.detail.setWordWrap(True)
        for label in (self.title, self.message, self.detail):
            label.setTextFormat(Qt.TextFormat.PlainText)
            layout.addWidget(label)
        self.hold = QTimer(self)
        self.hold.setSingleShot(True)
        self.hold.timeout.connect(self._fade_out)
        self.fade = QPropertyAnimation(self, b'windowOpacity', self)
        self.fade.setDuration(self.FADE_MS)
        self.fade.finished.connect(self._fade_finished)
        self._fading_out = False

    def notify(self, message, error=False):
        # Replace an existing success immediately if a later save error arrives.
        self.hold.stop()
        self.fade.stop()
        self._fading_out = False
        self.message.setText('Error' if error else message)
        detail = ' '.join(str(message).split())
        self.detail.setText(detail[:237] + '…' if len(detail) > 240 else detail)
        self.detail.setVisible(error)
        self.hold.setInterval(self.ERROR_HOLD_MS if error else self.HOLD_MS)
        screen = QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
        area = screen.availableGeometry()
        preferred_width = max(260, min(360, self.message.fontMetrics().horizontalAdvance(self.message.text()) + 32))
        self.setFixedWidth(min(360 if error else preferred_width, max(120, area.width() - 36)))
        # adjustSize() can use an unconstrained label hint, leaving wrapped lines
        # clipped. Measure the layout at the actual popup width instead.
        layout = self.layout()
        layout.invalidate()
        layout.activate()
        self.resize(self.width(), max(layout.minimumSize().height(), layout.totalHeightForWidth(self.width())))
        layout.activate()
        self.move(area.right() - self.width() - 17, area.top() + 18)
        opacity = self.windowOpacity() if self.isVisible() else 0.0
        self.setWindowOpacity(opacity)
        # These flags map to a no-activate, click-through tool window on Windows.
        # Never activateWindow()/raise_(): the game must retain keyboard focus.
        self.show()
        self.fade.setStartValue(opacity)
        self.fade.setEndValue(1.0)
        self.fade.start()

    def _fade_out(self):
        self._fading_out = True
        self.fade.setStartValue(self.windowOpacity())
        self.fade.setEndValue(0.0)
        self.fade.start()

    def _fade_finished(self):
        if self._fading_out:
            self.hide()
            self.dismissed.emit()
            self.close()  # Deletes the widget and its native window after this event.
        else:
            self.hold.start()
