"""Use Windows' standard popup menu renderer for the tray menu."""
from __future__ import annotations

import ctypes
from ctypes import wintypes

from PySide6.QtCore import QTimer


def windows_api():
    api = ctypes.WinDLL('user32', use_last_error=True)
    api.CreatePopupMenu.argtypes = []
    api.CreatePopupMenu.restype = wintypes.HMENU
    api.AppendMenuW.argtypes = [wintypes.HMENU, wintypes.UINT, ctypes.c_size_t, wintypes.LPCWSTR]
    api.AppendMenuW.restype = wintypes.BOOL
    api.DestroyMenu.argtypes = [wintypes.HMENU]
    api.DestroyMenu.restype = wintypes.BOOL
    api.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
    api.GetCursorPos.restype = wintypes.BOOL
    api.SetForegroundWindow.argtypes = [wintypes.HWND]
    api.SetForegroundWindow.restype = wintypes.BOOL
    api.TrackPopupMenuEx.argtypes = [wintypes.HMENU, wintypes.UINT, ctypes.c_int,
                                     ctypes.c_int, wintypes.HWND, ctypes.c_void_p]
    api.TrackPopupMenuEx.restype = wintypes.UINT
    api.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, ctypes.c_size_t, ctypes.c_ssize_t]
    api.PostMessageW.restype = wintypes.BOOL
    return api


class NativeMenu:
    def __init__(self, backend=None):
        self.api = backend if backend is not None else windows_api()
        self.commands = {}
        self.showing = False

    def _create(self, menu):
        handle = self.api.CreatePopupMenu()
        if not handle:
            raise RuntimeError('Windows could not create the tray menu.')
        try:
            for action in menu.actions():
                if not action.isVisible():
                    continue
                if action.isSeparator():
                    success = self.api.AppendMenuW(handle, 0x800, 0, None)
                else:
                    flags = (0 if action.isEnabled() else 1) | (8 if action.isChecked() else 0)
                    if action.menu() is not None:
                        child = self._create(action.menu())
                        success = self.api.AppendMenuW(handle, flags | 0x10, child, action.text())
                        if not success:
                            self.api.DestroyMenu(child)
                    else:
                        identifier = len(self.commands) + 1
                        self.commands[identifier] = action
                        success = self.api.AppendMenuW(handle, flags, identifier, action.text())
                if not success:
                    raise RuntimeError('Windows could not populate the tray menu.')
            return handle
        except Exception:
            self.api.DestroyMenu(handle)  # Also releases attached submenus.
            raise

    def show(self, menu, owner):
        if self.showing:
            return
        self.showing = True
        self.commands = {}
        handle = None
        chosen = None
        try:
            handle = self._create(menu)
            point = wintypes.POINT()
            if not self.api.GetCursorPos(ctypes.byref(point)):
                raise RuntimeError('Windows could not locate the tray menu position.')
            window = int(owner.winId())
            self.api.SetForegroundWindow(window)
            # Plain strings/separators only: Windows owns all visual rendering.
            identifier = self.api.TrackPopupMenuEx(handle, 0x100 | 0x80 | 0x02,
                point.x, point.y, window, None)
            self.api.PostMessageW(window, 0, 0, 0)
            chosen = self.commands.get(identifier)
        finally:
            if handle:
                self.api.DestroyMenu(handle)
            self.commands = {}
            self.showing = False
        # Return from the native modal menu before opening dialogs or quitting.
        if chosen is not None and chosen.isEnabled():
            QTimer.singleShot(0, chosen.trigger)
