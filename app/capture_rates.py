"""Capture presets and explicit display-refresh matching for live sources."""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import math
import sys

from PySide6.QtGui import QGuiApplication


def validate_rate(fps):
    if not isinstance(fps, (int, float)) or not math.isfinite(fps) or fps <= 0:
        raise ValueError('Choose a positive, finite capture frame rate.')


def monitor_name(handle):
    class Info(ctypes.Structure):
        _fields_ = [('size', wintypes.DWORD), ('monitor', wintypes.RECT),
                    ('work', wintypes.RECT), ('flags', wintypes.DWORD), ('device', wintypes.WCHAR * 32)]
    user = ctypes.WinDLL('user32')
    user.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
    user.MonitorFromWindow.restype = wintypes.HMONITOR
    user.GetMonitorInfoW.argtypes = [wintypes.HMONITOR, ctypes.POINTER(Info)]
    monitor = user.MonitorFromWindow(handle, 2)
    info = Info()
    info.size = ctypes.sizeof(info)
    if not user.GetMonitorInfoW(monitor, ctypes.byref(info)):
        raise RuntimeError('Could not inspect the selected display. Choose a fixed frame rate.')
    return info.device


def monitor_rate(name):
    """Read the active rational refresh rate without relying on friendly names."""
    class Luid(ctypes.Structure):
        _fields_ = [('low', wintypes.DWORD), ('high', wintypes.LONG)]
    class Rational(ctypes.Structure):
        _fields_ = [('numerator', wintypes.UINT), ('denominator', wintypes.UINT)]
    class Source(ctypes.Structure):
        _fields_ = [('adapter', Luid), ('id', wintypes.UINT), ('mode', wintypes.UINT), ('flags', wintypes.UINT)]
    class Target(ctypes.Structure):
        _fields_ = [('adapter', Luid), ('id', wintypes.UINT), ('mode', wintypes.UINT),
                    ('technology', ctypes.c_int), ('rotation', ctypes.c_int), ('scaling', ctypes.c_int),
                    ('refresh', Rational), ('scanline', ctypes.c_int), ('available', wintypes.BOOL),
                    ('flags', wintypes.UINT)]
    class Path(ctypes.Structure):
        _fields_ = [('source', Source), ('target', Target), ('flags', wintypes.UINT)]
    class DeviceName(ctypes.Structure):
        _fields_ = [('type', wintypes.UINT), ('size', wintypes.UINT), ('adapter', Luid),
                    ('id', wintypes.UINT), ('name', wintypes.WCHAR * 32)]
    user = ctypes.WinDLL('user32')
    user.GetDisplayConfigBufferSizes.argtypes = [wintypes.UINT, ctypes.POINTER(wintypes.UINT), ctypes.POINTER(wintypes.UINT)]
    user.QueryDisplayConfig.argtypes = [wintypes.UINT, ctypes.POINTER(wintypes.UINT), ctypes.POINTER(Path),
                                       ctypes.POINTER(wintypes.UINT), ctypes.c_void_p, ctypes.c_void_p]
    user.DisplayConfigGetDeviceInfo.argtypes = [ctypes.POINTER(DeviceName)]
    for _ in range(3):
        path_count, mode_count = wintypes.UINT(), wintypes.UINT()
        if user.GetDisplayConfigBufferSizes(2, ctypes.byref(path_count), ctypes.byref(mode_count)):
            break
        paths = (Path * path_count.value)()
        modes = ctypes.create_string_buffer(mode_count.value * 64)  # DISPLAYCONFIG_MODE_INFO
        result = user.QueryDisplayConfig(2, ctypes.byref(path_count), paths, ctypes.byref(mode_count), modes, None)
        if result == 122:
            continue  # Display configuration changed between the two calls.
        if result:
            break
        for path in paths[:path_count.value]:
            device = DeviceName()
            device.type, device.size = 1, ctypes.sizeof(device)
            device.adapter, device.id = path.source.adapter, path.source.id
            if not user.DisplayConfigGetDeviceInfo(ctypes.byref(device)) and device.name.casefold() == name.casefold():
                rate = path.target.refresh
                if rate.denominator:
                    value = rate.numerator / rate.denominator
                    validate_rate(value)
                    return value
        break
    raise RuntimeError('Could not determine the selected display refresh rate. Choose a fixed frame rate.')


def resolve_rate(preference, geometry):
    if preference:
        validate_rate(preference)
        return preference
    selected = getattr(geometry, 'windows', ())
    if selected and sys.platform == 'win32':
        names = {monitor_name(window.handle).casefold() for window in selected}
        fps = max(monitor_rate(name) for name in names)
        return int(fps) if float(fps).is_integer() else fps
    else:
        screens = [QGuiApplication.primaryScreen()] if QGuiApplication.primaryScreen() else []
    if not screens:
        raise RuntimeError('Could not detect the source refresh rate. Choose a fixed frame rate.')
    # One mixed video needs one sampling rate. Use the fastest selected display.
    fps = max(screen.refreshRate() for screen in screens)
    validate_rate(fps)
    return int(fps) if float(fps).is_integer() else fps
