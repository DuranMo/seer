from __future__ import annotations

import ctypes
from ctypes import wintypes
import sys
from itertools import count

from PySide6.QtCore import QAbstractNativeEventFilter, QObject, Qt, Signal
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import QApplication

DEFAULT_KEYBIND = 'Ctrl+Shift+R'
DEFAULT_CLIP_KEYBIND = 'Ctrl+Shift+F8'
_hotkey_ids = count(0x5345, 2)
WM_HOTKEY = 0x0312


def native_shortcut(text: str) -> tuple[int, int]:
    sequence = QKeySequence.fromString(text, QKeySequence.SequenceFormat.PortableText)
    if sequence.count() != 1:
        raise ValueError('Choose one shortcut, such as Ctrl+Shift+R.')
    combination = sequence[0]
    key, modifiers = combination.key(), combination.keyboardModifiers()
    flags = 0
    for modifier, flag in ((Qt.KeyboardModifier.ControlModifier, 2),
                           (Qt.KeyboardModifier.AltModifier, 1),
                           (Qt.KeyboardModifier.ShiftModifier, 4),
                           (Qt.KeyboardModifier.MetaModifier, 8)):
        if modifiers & modifier:
            flags |= flag
    value = key.value
    if Qt.Key.Key_A.value <= value <= Qt.Key.Key_Z.value or Qt.Key.Key_0.value <= value <= Qt.Key.Key_9.value:
        vk = value
        if not flags:
            raise ValueError('Add Ctrl, Alt, Shift, or Win to a letter or number.')
    elif Qt.Key.Key_F1.value <= value <= Qt.Key.Key_F24.value:
        vk = 0x70 + value - Qt.Key.Key_F1.value
        if key == Qt.Key.Key_F12:
            raise ValueError('Windows reserves F12. Choose another key.')
    else:
        keys = {Qt.Key.Key_Space: 0x20, Qt.Key.Key_Tab: 0x09, Qt.Key.Key_Return: 0x0D,
                Qt.Key.Key_Insert: 0x2D, Qt.Key.Key_Delete: 0x2E, Qt.Key.Key_Home: 0x24,
                Qt.Key.Key_End: 0x23, Qt.Key.Key_PageUp: 0x21, Qt.Key.Key_PageDown: 0x22,
                Qt.Key.Key_Left: 0x25, Qt.Key.Key_Up: 0x26, Qt.Key.Key_Right: 0x27,
                Qt.Key.Key_Down: 0x28, Qt.Key.Key_Pause: 0x13, Qt.Key.Key_ScrollLock: 0x91}
        if key not in keys:
            raise ValueError('Use a letter, number, function key, or navigation key.')
        vk = keys[key]
    return flags | 0x4000, vk


class _NativeFilter(QAbstractNativeEventFilter):
    def __init__(self, owner):
        super().__init__()
        self.owner = owner

    def nativeEventFilter(self, event_type, message):
        if bytes(event_type) in (b'windows_generic_MSG', b'windows_dispatcher_MSG'):
            msg = wintypes.MSG.from_address(int(message))
            if msg.message == WM_HOTKEY and msg.wParam == self.owner.identifier:
                self.owner.activated.emit()
                return True, 0
        return False, 0


class GlobalHotkey(QObject):
    activated = Signal()

    def __init__(self, parent=None, backend=None):
        super().__init__(parent)
        self.backend = backend
        if backend is None and sys.platform == 'win32':
            self.backend = ctypes.WinDLL('user32', use_last_error=True)
            self.backend.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
            self.backend.RegisterHotKey.restype = wintypes.BOOL
            self.backend.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
            self.backend.UnregisterHotKey.restype = wintypes.BOOL
        self.identifier = None
        base = next(_hotkey_ids)
        if base + 1 > 0xBFFF:
            raise RuntimeError('Too many shortcut registrations.')
        self.identifiers = (base, base + 1)
        self.text = ''
        self.filter = _NativeFilter(self)
        QApplication.instance().installNativeEventFilter(self.filter)

    def set_shortcut(self, text: str):
        modifiers, key = native_shortcut(text)
        canonical = QKeySequence(text).toString(QKeySequence.SequenceFormat.PortableText)
        if canonical == self.text and self.identifier is not None:
            return
        if self.backend is None:
            raise RuntimeError('Global recording shortcuts require Windows.')
        # Keep the previous binding working if the replacement is unavailable.
        candidate = self.identifiers[1] if self.identifier == self.identifiers[0] else self.identifiers[0]
        if not self.backend.RegisterHotKey(None, candidate, modifiers, key):
            raise RuntimeError('That shortcut is unavailable or already used by another app. Choose another.')
        if self.identifier is not None:
            self.backend.UnregisterHotKey(None, self.identifier)
        self.identifier, self.text = candidate, canonical

    def unregister(self):
        if self.identifier is not None:
            self.backend.UnregisterHotKey(None, self.identifier)
            self.identifier = None

    def close(self):
        self.unregister()
        QApplication.instance().removeNativeEventFilter(self.filter)
