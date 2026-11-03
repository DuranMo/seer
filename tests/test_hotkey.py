from __future__ import annotations

import ctypes
from ctypes import wintypes
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import pytest
from PySide6.QtWidgets import QApplication

from app.hotkey import GlobalHotkey, WM_HOTKEY, native_shortcut


class Backend:
    def __init__(self):
        self.registered = {}
        self.fail = False

    def RegisterHotKey(self, window, identifier, modifiers, key):
        if self.fail:
            return False
        self.registered[identifier] = (modifiers, key)
        return True

    def UnregisterHotKey(self, window, identifier):
        self.registered.pop(identifier, None)
        return True


@pytest.mark.parametrize('text,expected', [('Ctrl+Shift+R', (0x4006, 0x52)),
    ('Alt+F9', (0x4001, 0x78)), ('F8', (0x4000, 0x77)), ('Ctrl+Space', (0x4002, 0x20))])
def test_native_key_mapping(text, expected):
    assert native_shortcut(text) == expected


@pytest.mark.parametrize('text', ['', 'R', 'Ctrl+K, Ctrl+C', 'F12'])
def test_invalid_shortcuts(text):
    with pytest.raises(ValueError):
        native_shortcut(text)


def test_keybind_conflict_keeps_old_binding_and_filters_messages():
    app = QApplication.instance() or QApplication([])
    backend = Backend()
    hotkey = GlobalHotkey(backend=backend)
    hotkey.set_shortcut('Ctrl+Shift+R')
    original = hotkey.identifier
    backend.fail = True
    with pytest.raises(RuntimeError):
        hotkey.set_shortcut('Alt+F9')
    assert hotkey.identifier == original
    assert hotkey.text == 'Ctrl+Shift+R'
    assert original in backend.registered
    backend.fail = False
    hotkey.set_shortcut('Alt+F9')
    assert original not in backend.registered
    calls = []
    hotkey.activated.connect(lambda: calls.append('toggle'))
    message = wintypes.MSG()
    message.message = WM_HOTKEY
    message.wParam = hotkey.identifier
    assert hotkey.filter.nativeEventFilter(b'windows_dispatcher_MSG', ctypes.addressof(message)) == (True, 0)
    assert calls == ['toggle']
    message.wParam = 99
    assert hotkey.filter.nativeEventFilter(b'windows_dispatcher_MSG', ctypes.addressof(message)) == (False, 0)
    hotkey.close()
    assert not backend.registered


def test_record_and_clip_hotkeys_have_independent_windows_ids():
    app = QApplication.instance() or QApplication([])
    backend = Backend()
    recording = GlobalHotkey(backend=backend)
    clipping = GlobalHotkey(backend=backend)
    recording.set_shortcut('Ctrl+Shift+R')
    clipping.set_shortcut('Ctrl+Shift+F8')
    assert recording.identifier != clipping.identifier
    assert len(backend.registered) == 2
    clipping.unregister()
    assert recording.identifier in backend.registered
    clipping.set_shortcut('Ctrl+Shift+F8')
    assert len(backend.registered) == 2
    recording.close()
    clipping.close()
