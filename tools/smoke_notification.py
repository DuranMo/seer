"""Opt-in Windows popup check: foreground, click-through, disposal, and memory."""
from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
import json
from pathlib import Path
import queue
import subprocess
import sys
import threading


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def target_child():
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication, QLineEdit

    app = QApplication([])
    edit = QLineEdit('Seer notification focus test: this window must keep keyboard focus.')
    edit.setWindowTitle('Seer popup test target')
    edit.showMaximized()
    edit.activateWindow()
    edit.setFocus()
    QTimer.singleShot(200, lambda: print(json.dumps({'hwnd': int(edit.winId())}), flush=True))
    QTimer.singleShot(60000, app.quit)
    return app.exec()


class Counters(ctypes.Structure):
    _fields_ = [('cb', wintypes.DWORD), ('PageFaultCount', wintypes.DWORD)] + [
        (name, ctypes.c_size_t) for name in ('PeakWorkingSetSize', 'WorkingSetSize',
        'QuotaPeakPagedPoolUsage', 'QuotaPagedPoolUsage', 'QuotaPeakNonPagedPoolUsage',
        'QuotaNonPagedPoolUsage', 'PagefileUsage', 'PeakPagefileUsage', 'PrivateUsage')]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--repeats', type=int, default=12)
    parser.add_argument('--target-child', action='store_true', help=argparse.SUPPRESS)
    options = parser.parse_args()
    if sys.platform != 'win32':
        parser.error('This check requires a real Windows desktop.')
    if options.target_child:
        return target_child()
    if options.repeats < 1:
        parser.error('--repeats must be positive.')

    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication
    import shiboken6

    app = QApplication([])
    app.setQuitOnLastWindowClosed(False)
    user = ctypes.WinDLL('user32', use_last_error=True)
    user.GetForegroundWindow.restype = wintypes.HWND
    user.SetForegroundWindow.argtypes = [wintypes.HWND]
    user.SetForegroundWindow.restype = wintypes.BOOL
    user.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    user.WindowFromPoint.argtypes = [wintypes.POINT]
    user.WindowFromPoint.restype = wintypes.HWND
    user.IsChild.argtypes = [wintypes.HWND, wintypes.HWND]
    user.IsWindow.argtypes = [wintypes.HWND]
    get_style = user.GetWindowLongPtrW if ctypes.sizeof(ctypes.c_void_p) == 8 else user.GetWindowLongW
    get_style.argtypes = [wintypes.HWND, ctypes.c_int]
    get_style.restype = ctypes.c_ssize_t
    psapi = ctypes.WinDLL('psapi', use_last_error=True)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
    original_foreground = user.GetForegroundWindow()
    samples, errors, windows, handles = [], [], [], []
    target = None
    active = None
    round_index = 0
    focus_checks = 0
    click_checks = 0
    foreground_target = 'temporary test window'
    native_style = None
    ended = False

    def memory(stage):
        counters = Counters()
        counters.cb = ctypes.sizeof(counters)
        if not psapi.GetProcessMemoryInfo(kernel.GetCurrentProcess(), ctypes.byref(counters), counters.cb):
            raise ctypes.WinError(ctypes.get_last_error())
        samples.append({'stage': stage, 'working_set_mib': round(counters.WorkingSetSize / 1048576, 3),
                        'private_commit_mib': round(counters.PrivateUsage / 1048576, 3)})

    def fail(message):
        if message not in errors:
            errors.append(message)

    def inspect():
        nonlocal focus_checks, click_checks, native_style
        if ended or target is None:
            return
        focus_checks += 1
        if user.GetForegroundWindow() != target:
            fail('The foreground window changed while the popup was displayed or fading.')
        if active is None or not shiboken6.isValid(active) or not active.isVisible():
            return
        handle = int(active.winId())
        style = get_style(handle, -20)
        native_style = f'0x{style:X}'
        required = 0x08000000 | 0x00000020 | 0x00000080  # NOACTIVATE, TRANSPARENT, TOOLWINDOW
        if style & required != required:
            fail(f'Popup lacks required non-activation/input styles: 0x{style:X}.')
        rect = wintypes.RECT()
        if user.GetWindowRect(handle, ctypes.byref(rect)):
            point = wintypes.POINT((rect.left + rect.right) // 2, (rect.top + rect.bottom) // 2)
            under = user.WindowFromPoint(point)
            click_checks += 1
            if under == handle or user.IsChild(handle, under):
                fail('Popup intercepted hit testing instead of passing through.')
            elif not under:
                fail('No underlying window was found for the click-through check.')

    def finish():
        nonlocal ended
        if ended:
            return
        ended = True
        monitor.stop()
        try:
            memory('after_repeated_popups')
            if any(shiboken6.isValid(window) for window in windows):
                fail('A notification widget remained alive after dismissal.')
            if any(user.IsWindow(handle) for handle in handles):
                fail('A notification HWND remained alive after dismissal.')
            if not focus_checks or not click_checks:
                fail('No foreground or click-through checks ran.')
        except Exception as error:
            fail(str(error))
        app.exit(int(bool(errors)))

    def next_popup():
        nonlocal active, round_index
        try:
            if round_index == 1:
                memory('after_first_popup')
            if round_index >= options.repeats:
                QTimer.singleShot(150, finish)
                return
            from app.notification import NotificationPopup
            active = NotificationPopup()
            windows.append(active)
            # Keep the first normal-length fade. Accelerate the allocation stress rounds.
            if round_index:
                active.FADE_MS = 40
                active.HOLD_MS = 80
                active.ERROR_HOLD_MS = 80
            active.dismissed.connect(lambda: QTimer.singleShot(30, next_popup))
            active.notify('Disk full: notification test' if round_index % 2 else 'Clip saved',
                          error=bool(round_index % 2))
            handles.append(int(active.winId()))
            round_index += 1
            if round_index == 1:
                QTimer.singleShot(300, shown)
        except Exception as error:
            fail(str(error))
            finish()

    def shown():
        try:
            memory('first_popup_visible')
            destination = Path('.tools/checks/notification.png')
            destination.parent.mkdir(parents=True, exist_ok=True)
            if active is None or not shiboken6.isValid(active) or not active.grab().save(str(destination)):
                fail('Could not save the notification preview.')
        except Exception as error:
            fail(str(error))

    def start():
        nonlocal target, foreground_target
        try:
            memory('before_popup_import')
            if not user.SetForegroundWindow(target) and user.GetForegroundWindow() != target:
                # Activation is intentionally restricted by Windows. Check that the
                # existing foreground app stays focused rather than bypassing it.
                target = user.GetForegroundWindow()
                foreground_target = 'existing foreground window (test activation refused)'
                if not target or not user.IsWindow(target):
                    raise RuntimeError('There is no foreground window to validate focus against.')
            monitor.start(10)
            next_popup()
        except Exception as error:
            fail(str(error))
            finish()

    monitor = QTimer()
    monitor.timeout.connect(inspect)
    child = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), '--target-child'],
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                             creationflags=subprocess.CREATE_NO_WINDOW)
    try:
        lines = queue.Queue()
        threading.Thread(target=lambda: lines.put(child.stdout.readline()), daemon=True).start()
        target = int(json.loads(lines.get(timeout=10))['hwnd'])
        QTimer.singleShot(200, start)
        QTimer.singleShot(45000, lambda: (fail('Notification check timed out.'), finish()))
        app.exec()
    except Exception as error:
        fail(str(error))
    finally:
        if child.poll() is None:
            child.terminate()
        child.wait(timeout=5)
        if original_foreground and user.IsWindow(original_foreground):
            user.SetForegroundWindow(original_foreground)

    result = {'passed': not errors, 'notifications': round_index, 'foreground_checks': focus_checks,
              'click_through_checks': click_checks, 'foreground_target': foreground_target,
              'native_extended_style': native_style, 'memory': samples, 'errors': errors}
    if samples:
        result['retained_private_mib'] = round(samples[-1]['private_commit_mib'] - samples[0]['private_commit_mib'], 3)
    rendered = json.dumps(result, indent=2)
    print(rendered, flush=True)
    if options.output:
        options.output.parent.mkdir(parents=True, exist_ok=True)
        options.output.write_text(rendered + '\n', encoding='utf-8')
    return int(bool(errors))


if __name__ == '__main__':
    raise SystemExit(main())
