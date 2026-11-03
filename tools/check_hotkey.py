"""Check native registration and Qt delivery without sending keyboard input."""
from __future__ import annotations

import ctypes
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from app.hotkey import GlobalHotkey, WM_HOTKEY


app = QApplication([])
app.setQuitOnLastWindowClosed(False)
hotkey = GlobalHotkey()
hotkey.set_shortcut('Ctrl+Alt+Shift+F10')
thread_id = ctypes.windll.kernel32.GetCurrentThreadId()


def delivered():
    print('OK: Windows registered the shortcut and Qt received WM_HOTKEY.', flush=True)
    hotkey.close()
    app.exit(0)


def post():
    if not ctypes.windll.user32.PostThreadMessageW(thread_id, WM_HOTKEY, hotkey.identifier, 0):
        print('Could not post the native hotkey message.', flush=True)
        hotkey.close()
        app.exit(1)


hotkey.activated.connect(delivered)
QTimer.singleShot(0, post)
QTimer.singleShot(3000, lambda: app.exit(1))
result = app.exec()
hotkey.close()
raise SystemExit(result)
