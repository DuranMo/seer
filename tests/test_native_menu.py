from __future__ import annotations

import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtWidgets import QApplication, QMenu, QWidget

from app.native_menu import NativeMenu


class Windows:
    def __init__(self):
        self.items = {}
        self.destroyed = []
        self.selection = 1

    def CreatePopupMenu(self):
        handle = len(self.items) + 1
        self.items[handle] = []
        return handle

    def AppendMenuW(self, handle, flags, identifier, text):
        self.items[handle].append((flags, identifier, text))
        return True

    def DestroyMenu(self, handle):
        self.destroyed.append(handle)

    def GetCursorPos(self, pointer):
        return True

    def SetForegroundWindow(self, window):
        return True

    def TrackPopupMenuEx(self, *args):
        return self.selection

    def PostMessageW(self, *args):
        return True


def test_native_menu_dispatches_actions_and_preserves_submenu_state():
    app = QApplication.instance() or QApplication([])
    owner, menu = QWidget(), QMenu()
    calls = []
    menu.addAction('Record', lambda: calls.append('record'))
    menu.addSeparator()
    submenu = menu.addMenu('Recording')
    checked = submenu.addAction('30 FPS')
    checked.setCheckable(True)
    checked.setChecked(True)
    disabled = submenu.addAction('Unavailable device')
    disabled.setEnabled(False)
    backend = Windows()
    popup = NativeMenu(backend)
    popup.show(menu, owner)
    assert not calls  # Actions wait for the native menu to finish closing.
    app.processEvents()
    assert calls == ['record']
    assert backend.items[1][1][0] == 0x800
    assert backend.items[1][2][0] & 0x10
    assert backend.items[2][0][0] & 8
    assert backend.items[2][1][0] & 1
    assert backend.destroyed == [1]
    assert not popup.commands and not popup.showing
    backend.selection = 0  # Clicking outside dismisses without running a command.
    popup.show(menu, owner)
    app.processEvents()
    assert calls == ['record']
    owner.close()
