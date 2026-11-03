"""Measure real Windows process memory at startup, preview, close, and reopen."""
from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import sys

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--clip', type=Path, required=True)
    options = parser.parse_args()
    tools_folder = Path(__file__).resolve().parents[1] / 'bin'
    os.environ.setdefault('SEER_FFMPEG', str(tools_folder / 'ffmpeg.exe'))
    os.environ.setdefault('SEER_FFPROBE', str(tools_folder / 'ffprobe.exe'))
    sys.path.insert(0, str(options.root.resolve()))

    from PySide6.QtCore import QObject, QSettings, QTimer, Signal
    from PySide6.QtWidgets import QApplication
    from app.main_window import MainWindow
    from app.styles import apply_colors


    class Counters(ctypes.Structure):
        _fields_ = [('cb', wintypes.DWORD), ('PageFaultCount', wintypes.DWORD)] + [
            (name, ctypes.c_size_t) for name in ('PeakWorkingSetSize', 'WorkingSetSize',
            'QuotaPeakPagedPoolUsage', 'QuotaPagedPoolUsage', 'QuotaPeakNonPagedPoolUsage',
            'QuotaNonPagedPoolUsage', 'PagefileUsage', 'PeakPagefileUsage', 'PrivateUsage')]


    class SilentHotkey(QObject):
        activated = Signal()
        text = 'Ctrl+Shift+R'

        def set_shortcut(self, text):
            self.text = text

        def close(self):
            pass


    app = QApplication([])
    app.setQuitOnLastWindowClosed(False)
    apply_colors(app)
    settings = QSettings(str(options.output.with_suffix('.ini').resolve()), QSettings.Format.IniFormat)
    window = MainWindow(settings=settings, hotkey=SilentHotkey())
    window.tray.hide()  # Do not add a second tray icon or register another real shortcut.
    samples = []
    errors = []
    psapi = ctypes.WinDLL('psapi', use_last_error=True)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]


    def sample(stage):
        counters = Counters()
        counters.cb = ctypes.sizeof(counters)
        if not psapi.GetProcessMemoryInfo(kernel.GetCurrentProcess(), ctypes.byref(counters), counters.cb):
            raise ctypes.WinError(ctypes.get_last_error())
        parent_ws, parent_private = counters.WorkingSetSize, counters.PrivateUsage
        child_ws = child_private = 0
        if window.editor is not None and not window.editor.finished:
            kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
            kernel.OpenProcess.restype = wintypes.HANDLE
            kernel.CloseHandle.argtypes = [wintypes.HANDLE]
            handle = kernel.OpenProcess(0x410, False, window.editor.process.pid)
            if handle:
                child = Counters()
                child.cb = ctypes.sizeof(child)
                if psapi.GetProcessMemoryInfo(handle, ctypes.byref(child), child.cb):
                    child_ws, child_private = child.WorkingSetSize, child.PrivateUsage
                kernel.CloseHandle(handle)
        data = {'stage': stage, 'working_set_mib': round((parent_ws + child_ws) / 1048576, 2),
                'parent_working_set_mib': round(parent_ws / 1048576, 2),
                'child_working_set_mib': round(child_ws / 1048576, 2),
                'private_commit_mib': round((parent_private + child_private) / 1048576, 2),
                'qt_multimedia_imported': 'PySide6.QtMultimedia' in sys.modules,
                'editor_exists': window.editor is not None}
        samples.append(data)
        print(json.dumps(data), flush=True)


    def startup():
        sample('startup')
        window._open_clip(options.clip.resolve(), temporary=False)
        await_loaded()


    def await_loaded():
        if window.editor is None or window.editor.loading:
            QTimer.singleShot(100, await_loaded)
            return
        if window.editor.media is None:
            errors.append('The sample video failed to load.')
            finish()
            return
        window.editor.send('play')
        QTimer.singleShot(1600, preview)


    def preview():
        sample('preview')
        window.close()
        QTimer.singleShot(2000, closed)


    def closed():
        if window.editor is not None:
            QTimer.singleShot(100, closed)
            return
        sample('closed')
        window._show_editor()
        QTimer.singleShot(2000, reopened)


    def reopened():
        if window.editor is None or window.editor.loading:
            QTimer.singleShot(100, reopened)
            return
        sample('reopened')
        window.close()
        QTimer.singleShot(2000, finish)


    def finish():
        sample('closed_again')
        options.output.write_text(json.dumps({'samples': samples, 'errors': errors}, indent=2), encoding='utf-8')
        window._request_quit()


    QTimer.singleShot(1500, startup)
    QTimer.singleShot(45000, lambda: (errors.append('Profile timed out.'), finish()))
    app.exec()
    return int(bool(errors))


if __name__ == '__main__':
    raise SystemExit(main())
