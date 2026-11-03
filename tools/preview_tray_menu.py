"""Briefly show and capture the real Windows menu, then dismiss it."""
from __future__ import annotations

import ctypes
from pathlib import Path
import sys
import threading

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QMenu, QWidget

from app.native_menu import NativeMenu
from app.styles import apply_colors


app = QApplication([])
app.setQuitOnLastWindowClosed(False)
apply_colors(app)
owner = QWidget()
window = int(owner.winId())
menu = QMenu()
menu.addAction('Start recording (Ctrl+Shift+R)')
menu.addAction('Keybind…')
recording = menu.addMenu('Recording')
recording.addMenu('Frame rate').addAction('30 FPS')
recording.addMenu('Audio').addAction('No audio')
menu.addSeparator()
menu.addAction('Open video…')
menu.addAction('Show editor').setEnabled(False)
menu.addSeparator()
menu.addAction('Quit')
popup = NativeMenu()
popup.api.FindWindowW.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p]
popup.api.FindWindowW.restype = ctypes.c_void_p


def dismiss():
    popup.api.PostMessageW(window, 0x001F, 0, 0)  # WM_CANCELMODE


def capture():
    handle = popup.api.FindWindowW('#32768', None)
    if handle:
        result = app.primaryScreen().grabWindow(handle)
        result.save('.tools/tray-native.png')
        print('Captured Windows standard popup menu.', flush=True)
    dismiss()


def show():
    fallback = threading.Timer(2, dismiss)
    fallback.start()
    QTimer.singleShot(400, capture)
    try:
        popup.show(menu, owner)
        print('Native menu opened and dismissed cleanly.', flush=True)
    finally:
        fallback.cancel()
        app.quit()


QTimer.singleShot(0, show)
raise SystemExit(app.exec())
