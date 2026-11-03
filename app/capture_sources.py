"""Lightweight Windows identities. Enumeration runs only in the Sources dialog."""
from __future__ import annotations

import ctypes
from ctypes import wintypes
from dataclasses import dataclass, asdict
import json
import os
import sys


@dataclass(frozen=True)
class Window:
    handle: int
    pid: int
    created: int
    title: str


def process_created(pid: int) -> int:
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.OpenProcess(0x1000, False, pid)
    if not handle:
        return 0
    try:
        values = [wintypes.FILETIME() for _ in range(4)]
        if not kernel.GetProcessTimes(handle, *(ctypes.byref(v) for v in values)):
            return 0
        return values[0].dwLowDateTime | values[0].dwHighDateTime << 32
    finally:
        kernel.CloseHandle(handle)


def window_pid(handle: int) -> int:
    user = ctypes.WinDLL('user32')
    user.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    pid = wintypes.DWORD()
    user.GetWindowThreadProcessId(handle, ctypes.byref(pid))
    return pid.value


def windows(exclude=()) -> list[Window]:
    if sys.platform != 'win32':
        return []
    user = ctypes.WinDLL('user32')
    user.IsWindowVisible.argtypes = [wintypes.HWND]
    user.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    user.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    dwm = ctypes.WinDLL('dwmapi')
    dwm.DwmGetWindowAttribute.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
    result, excluded = [], {os.getpid(), *exclude}
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    @callback_type
    def visit(handle, _):
        if not user.IsWindowVisible(handle):
            return True
        length = user.GetWindowTextLengthW(handle)
        pid = window_pid(handle)
        if not length or pid in excluded:
            return True
        cloaked = wintypes.DWORD()
        if dwm.DwmGetWindowAttribute(handle, 14, ctypes.byref(cloaked), ctypes.sizeof(cloaked)) == 0 and cloaked.value:
            return True
        title = ctypes.create_unicode_buffer(length + 1)
        user.GetWindowTextW(handle, title, len(title))
        created = process_created(pid)
        if created:
            result.append(Window(int(handle), pid, created, title.value))
        return True

    user.EnumWindows.argtypes = [callback_type, wintypes.LPARAM]
    user.EnumWindows(visit, 0)
    return sorted(result, key=lambda w: w.title.casefold())


def validate(selected: tuple[Window, ...], *, audio=False):
    for window in selected:
        if (not audio and window_pid(window.handle) != window.pid) or process_created(window.pid) != window.created:
            raise RuntimeError(f'The selected app is no longer available: {window.title}. Choose Sources again.')


def load_selection(settings, key, *, strict=True) -> tuple[Window, ...]:
    try:
        value = json.loads(str(settings.value(key, '[]')))
        if not isinstance(value, list):
            raise ValueError('Expected a source list.')
        return tuple(Window(**item) for item in value)
    except (ValueError, TypeError, KeyError):
        if strict:
            raise RuntimeError('Saved capture sources are invalid. Choose Recording → Sources again.')
        return ()


def save_selection(settings, key, selected):
    settings.setValue(key, json.dumps([asdict(window) for window in selected]))


def root_process_ids(selected, parents=None):
    """Don't capture a child twice when its parent process tree is selected."""
    if parents is None:
        class Entry(ctypes.Structure):
            _fields_ = [('size', wintypes.DWORD), ('usage', wintypes.DWORD), ('pid', wintypes.DWORD),
                ('heap', ctypes.c_size_t), ('module', wintypes.DWORD), ('threads', wintypes.DWORD),
                ('parent', wintypes.DWORD), ('priority', wintypes.LONG), ('flags', wintypes.DWORD),
                ('exe', wintypes.WCHAR * 260)]
        kernel = ctypes.WinDLL('kernel32')
        kernel.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
        kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
        kernel.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(Entry)]
        kernel.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(Entry)]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        snapshot = kernel.CreateToolhelp32Snapshot(2, 0)
        if snapshot == ctypes.c_void_p(-1).value:
            raise RuntimeError('Windows could not inspect the selected audio process trees.')
        parents = {}
        try:
            entry = Entry()
            entry.size = ctypes.sizeof(entry)
            more = kernel.Process32FirstW(snapshot, ctypes.byref(entry))
            while more:
                parents[entry.pid] = entry.parent
                more = kernel.Process32NextW(snapshot, ctypes.byref(entry))
        finally:
            kernel.CloseHandle(snapshot)
    chosen = set(selected)
    roots = []
    for pid in dict.fromkeys(selected):
        ancestor, seen = parents.get(pid, 0), {pid}
        while ancestor and ancestor not in chosen and ancestor not in seen:
            seen.add(ancestor)
            ancestor = parents.get(ancestor, 0)
        if ancestor not in chosen:
            roots.append(pid)
    return tuple(roots)


class WindowRegion(tuple):
    def __new__(cls, geometry, selected):
        region = super().__new__(cls, geometry)
        region.windows = tuple(selected)
        return region


def region(geometry, selected):
    if not selected:
        return geometry
    validate(selected)
    if len(selected) == 1:
        rect = wintypes.RECT()
        user = ctypes.WinDLL('user32')
        user.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
        if not user.GetWindowRect(selected[0].handle, ctypes.byref(rect)):
            raise RuntimeError('Could not inspect the selected window.')
        width, height = rect.right - rect.left, rect.bottom - rect.top
        if width < 2 or height < 2 or width > 7680 or height > 4320:
            raise ValueError('Restore the selected window before starting capture.')
        geometry = (0, 0, width, height)
    return WindowRegion(geometry, selected)
