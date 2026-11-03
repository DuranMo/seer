"""Opt-in native tray -> hotkey -> capture -> small editor -> tray lifecycle check."""
from __future__ import annotations

import ctypes
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import QSettings, QTimer
from PySide6.QtWidgets import QApplication

from app.hotkey import WM_HOTKEY
from app.main_window import MainWindow
from app.styles import apply_colors


def main():
    app = QApplication([])
    app.setQuitOnLastWindowClosed(False)
    apply_colors(app)
    settings = QSettings(str(Path('.tools/smoke-tray.ini').resolve()), QSettings.Format.IniFormat)
    settings.setValue('keybind', 'Ctrl+Alt+Shift+F10')
    settings.setValue('recording/audio', '')
    window = MainWindow(settings=settings)
    assert not window.isVisible(), 'Startup should stay in the tray.'
    thread_id = ctypes.windll.kernel32.GetCurrentThreadId()
    passed = False
    captures = []


    def toggle():
        ctypes.windll.user32.PostThreadMessageW(thread_id, WM_HOTKEY, window.hotkey.identifier, 0)


    def stopped(path):
        captures.append(path)
        print('Finalized capture:', path, flush=True)
        window.editor.idle.connect(inspect)


    def inspect():
        if window.editor.media is not None:
            assert window.editor.process.is_alive(), 'The editor should open automatically.'
            window.editor.send('play')
            QTimer.singleShot(500, finish)


    def finish():
        window.close()
        QTimer.singleShot(100, closed)

    def closed():
        nonlocal passed
        if window.editor is not None:
            QTimer.singleShot(100, closed)
            return
        assert not window.isVisible(), 'Closing the editor should hide it.'
        assert window.tray.isVisible(), 'The background task should still have a tray icon.'
        passed = True
        print('OK: native shortcut toggled capture, opened the editor, and returned to the tray.', flush=True)
        window._request_quit()


    window.recorder.started.connect(lambda: QTimer.singleShot(2000, toggle))
    window.recorder.ready.connect(stopped)
    window.recorder.failed.connect(lambda message: print('FAILED:', message, flush=True))
    QTimer.singleShot(500, toggle)
    QTimer.singleShot(20000, window._request_quit)
    app.exec()
    if passed:
        assert captures and all(not path.exists() for path in captures), 'Quit should delete temporary captures.'
        print('OK: Quit removed the temporary recording after releasing playback.', flush=True)
    return 0 if passed else 1


if __name__ == '__main__':
    raise SystemExit(main())
