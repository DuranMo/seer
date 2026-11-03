from __future__ import annotations

import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtCore import QCoreApplication, QEvent, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from shiboken6 import isValid

from app.notification import NotificationPopup


def test_error_replaces_success_and_disposes_window_without_idle_timer():
    app = QApplication.instance() or QApplication([])
    popup = NotificationPopup()
    popup.fade.setDuration(10)
    popup.HOLD_MS = 20
    popup.ERROR_HOLD_MS = 120
    dismissed = []
    popup.dismissed.connect(lambda: dismissed.append(True))
    popup.notify('Clip saved')
    QTest.qWait(25)
    popup.notify('Disk full: <please free space>', error=True)
    QTest.qWait(50)  # The old success timeout must not dismiss the error.
    assert popup.isVisible()
    assert popup.title.text() == 'Seer'
    assert popup.message.text() == 'Error'
    assert popup.detail.text() == 'Disk full: <please free space>'
    assert popup.detail.textFormat() == Qt.TextFormat.PlainText
    assert not dismissed
    QTest.qWait(180)
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert dismissed == [True]
    assert not isValid(popup)  # Includes native window, animation, and hold timer.


def test_notification_route_reuses_active_popup_and_never_sends_windows_toast():
    from app.main_window import MainWindow

    app = QApplication.instance() or QApplication([])

    # Exercise the controller route without devices, hotkeys, or a tray icon.
    # A Windows showMessage fallback would fail: this controller has no tray.
    from PySide6.QtWidgets import QWidget
    owner = QWidget()
    # NotificationPopup requires a QWidget parent, like the real MainWindow.
    owner._notification = None
    owner._notification_dismissed = lambda: setattr(owner, '_notification', None)
    MainWindow._notify(owner, 'Clip saved')
    popup = owner._notification
    MainWindow._notify(owner, 'Could not save', True)
    assert owner._notification is popup
    assert popup.message.text() == 'Error'
    for flag in (Qt.WindowType.WindowDoesNotAcceptFocus,
                 Qt.WindowType.WindowTransparentForInput,
                 Qt.WindowType.WindowStaysOnTopHint):
        assert popup.windowFlags() & flag
    assert popup.testAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
    assert popup.focusPolicy() == Qt.FocusPolicy.NoFocus
    popup.close()
    owner.deleteLater()
    app.processEvents()
