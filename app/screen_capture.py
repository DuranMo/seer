"""Desktop Duplication when available, with a validated GDI fallback."""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import subprocess
from uuid import UUID

from .video_utils import executable, run_checked


def primary_output(geometry) -> int | None:
    """Find the primary monitor on D3D11's default adapter; never guess output 0."""
    class Description(ctypes.Structure):
        _fields_ = [('name', wintypes.WCHAR * 32), ('rect', wintypes.RECT),
                    ('attached', wintypes.BOOL), ('rotation', ctypes.c_uint),
                    ('monitor', wintypes.HMONITOR)]

    def method(pointer, index, result, *args):
        table = ctypes.cast(pointer, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
        return ctypes.WINFUNCTYPE(result, ctypes.c_void_p, *args)(table[index])

    def release(pointer):
        if pointer:
            method(pointer, 2, ctypes.c_ulong)(pointer)

    factory, adapter = ctypes.c_void_p(), ctypes.c_void_p()
    create = ctypes.WinDLL('dxgi').CreateDXGIFactory1
    create.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
    create.restype = ctypes.c_long
    iid = (ctypes.c_ubyte * 16).from_buffer_copy(UUID('770aae78-f26f-4dba-a829-253c83d1b387').bytes_le)
    try:
        if create(ctypes.byref(iid), ctypes.byref(factory)) < 0:
            return None
        if method(factory, 7, ctypes.c_long, ctypes.c_uint, ctypes.POINTER(ctypes.c_void_p))(
                factory, 0, ctypes.byref(adapter)) < 0:
            return None
        for index in range(32):
            output = ctypes.c_void_p()
            if method(adapter, 7, ctypes.c_long, ctypes.c_uint, ctypes.POINTER(ctypes.c_void_p))(
                    adapter, index, ctypes.byref(output)) < 0:
                break
            try:
                desc = Description()
                if method(output, 7, ctypes.c_long, ctypes.POINTER(Description))(output, ctypes.byref(desc)) < 0:
                    continue
                r = desc.rect
                if desc.attached and desc.rotation in (0, 1) and (
                        r.left, r.top, r.right - r.left, r.bottom - r.top) == tuple(geometry):
                    return index
            finally:
                release(output)
    finally:
        release(adapter)
        release(factory)
    return None


def input_args(fps, geometry, output: int | None = None):
    if output == 'windows':
        return ['-use_wallclock_as_timestamps', '1', '-f', 'rawvideo', '-pixel_format', 'bgra',
                '-video_size', f'{geometry[2]}x{geometry[3]}', '-framerate', str(fps), '-i', 'pipe:0']
    if output is not None:
        # Wall-clock packet timestamps provide the same epoch as WASAPI audio.
        # Download immediately: downstream encoders receive ordinary BGRA frames.
        return ['-use_wallclock_as_timestamps', '1', '-f', 'lavfi', '-i',
                f'ddagrab=output_idx={output}:framerate={fps}:draw_mouse=1,hwdownload,format=bgra']
    x, y, width, height = geometry
    return ['-f', 'gdigrab', '-framerate', str(fps), '-draw_mouse', '1',
            '-offset_x', str(x), '-offset_y', str(y), '-video_size',
            f'{width}x{height}', '-i', 'desktop']


def choose_capture(fps, geometry) -> int | None:
    if getattr(geometry, 'windows', ()):
        from .capture_sources import validate
        validate(geometry.windows)
        executable('seer-capture')
        return 'windows'
    tool = executable('ffmpeg')
    if 'ddagrab' in run_checked([tool, '-hide_banner', '-filters']):
        try:
            output = primary_output(geometry)
            if output is not None:
                run_checked([tool, '-hide_banner', '-v', 'error'] + input_args(fps, geometry, output)
                            + ['-frames:v', '2', '-f', 'null', '-'], timeout=10)
                return output
        except (OSError, RuntimeError, subprocess.TimeoutExpired):
            pass
    if 'gdigrab' not in run_checked([tool, '-hide_banner', '-devices']):
        raise RuntimeError('No supported Windows screen capture backend is available in this FFmpeg build.')
    return None
