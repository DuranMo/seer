"""Opt-in packaged-process diagnostic; never imported during normal startup."""
from __future__ import annotations

import ctypes
import json
from pathlib import Path
import sys

from PySide6.QtCore import QObject, QSettings, QTimer, Signal
from PySide6.QtWidgets import QApplication

from .main_window import MainWindow
from .styles import apply_colors


def run_check(clip: Path, output: Path) -> int:
    class Hotkey(QObject):
        activated = Signal()
        text = 'Ctrl+Shift+R'

        def set_shortcut(self, text):
            self.text = text

        def close(self):
            pass

    def memory():
        from ctypes import wintypes
        class Counters(ctypes.Structure):
            _fields_ = [('cb', wintypes.DWORD), ('faults', wintypes.DWORD)] + [
                (name, ctypes.c_size_t) for name in ('peak_ws', 'ws', 'peak_paged',
                'paged', 'peak_nonpaged', 'nonpaged', 'pagefile', 'peak_pagefile', 'private')]
        counters = Counters()
        counters.cb = ctypes.sizeof(counters)
        kernel = ctypes.WinDLL('kernel32')
        kernel.GetCurrentProcess.restype = wintypes.HANDLE
        api = ctypes.WinDLL('psapi')
        api.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
        if not api.GetProcessMemoryInfo(kernel.GetCurrentProcess(), ctypes.byref(counters), counters.cb):
            raise RuntimeError('Process memory measurement failed.')
        return {'working_set_mib': round(counters.ws / 1048576, 2),
                'private_commit_mib': round(counters.private / 1048576, 2),
                'multimedia_in_tray': 'PySide6.QtMultimedia' in sys.modules}

    app = QApplication([])
    app.setQuitOnLastWindowClosed(False)
    apply_colors(app)
    window = MainWindow(settings=QSettings(str(output.with_suffix('.ini')), QSettings.Format.IniFormat), hotkey=Hotkey())
    window.tray.hide()
    report = {'startup': memory(), 'cycles': []}
    errors = []
    count = 0
    waiting_close = False

    def tick():
        nonlocal count, waiting_close
        if window.editor is None:
            if waiting_close:
                report['cycles'].append(memory())
                count += 1
                waiting_close = False
                if count == 2:
                    finish()
                    return
                if window.clip_state is None:
                    errors.append('The editor did not return its state.')
            window._show_editor()
        elif window.editor.media is not None and not window.editor.loading:
            if not waiting_close:
                waiting_close = True
                window.editor.send('play')
                QTimer.singleShot(1000, window.close)

    def finish():
        timer.stop()
        report['errors'] = errors
        output.write_text(json.dumps(report, indent=2), encoding='utf-8')
        window._request_quit()

    window._editor_failed = errors.append
    window._open_clip(clip.resolve())
    timer = QTimer()
    timer.setInterval(100)
    timer.timeout.connect(tick)
    timer.start()
    QTimer.singleShot(20000, lambda: (errors.append('Editor diagnostic timed out.'), finish()))
    app.exec()
    return int(bool(errors))
