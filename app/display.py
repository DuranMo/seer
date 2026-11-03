from __future__ import annotations

import ctypes
import sys

from PySide6.QtGui import QGuiApplication


def primary_geometry() -> tuple[int, int, int, int]:
    if sys.platform == 'win32':
        user = ctypes.windll.user32
        class POINT(ctypes.Structure):
            _fields_ = [('x', ctypes.c_long), ('y', ctypes.c_long)]
        class RECT(ctypes.Structure):
            _fields_ = [('left', ctypes.c_long), ('top', ctypes.c_long),
                        ('right', ctypes.c_long), ('bottom', ctypes.c_long)]
        class MONITORINFO(ctypes.Structure):
            _fields_ = [('cbSize', ctypes.c_ulong), ('rcMonitor', RECT),
                        ('rcWork', RECT), ('dwFlags', ctypes.c_ulong)]
        user.MonitorFromPoint.argtypes = [POINT, ctypes.c_ulong]
        user.MonitorFromPoint.restype = ctypes.c_void_p
        user.GetMonitorInfoW.argtypes = [ctypes.c_void_p, ctypes.POINTER(MONITORINFO)]
        handle = user.MonitorFromPoint(POINT(0, 0), 1)
        info = MONITORINFO()
        info.cbSize = ctypes.sizeof(info)
        if not user.GetMonitorInfoW(handle, ctypes.byref(info)):
            raise RuntimeError('Windows could not inspect the primary display.')
        r = info.rcMonitor
        return r.left, r.top, r.right - r.left, r.bottom - r.top
    screen = QGuiApplication.primaryScreen()
    rect, scale = screen.geometry(), screen.devicePixelRatio()
    return tuple(round(v * scale) for v in (rect.x(), rect.y(), rect.width(), rect.height()))
