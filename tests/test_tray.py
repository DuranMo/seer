from __future__ import annotations

import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtCore import QCoreApplication, QEvent, QObject, QRectF, QSettings, QTimer, Signal
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import QApplication, QDialogButtonBox, QKeySequenceEdit

from app import main_window as module


class Hotkey(QObject):
    activated = Signal()

    def __init__(self):
        super().__init__()
        self.text = ''

    def set_shortcut(self, text):
        self.text = text

    def close(self):
        pass

    def unregister(self):
        pass


class Recorder(QObject):
    started = Signal()
    ready = Signal(object)
    failed = Signal(str)
    status = Signal(str)

    def __init__(self, parent):
        super().__init__(parent)
        self.busy = self.stopping = False
        self.audio = None
        self.start_count = 0

    def start(self, folder, fps, device, geometry):
        self.busy, self.stopping = True, False
        self.start_count += 1
        self.folder = folder
        self.started.emit()

    def stop(self):
        self.stopping = True
        self.busy = False
        self.ready.emit(self.folder / 'screen.mp4')


def test_hidden_start_toggle_to_editor_close_to_tray_and_persist_keybind(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])

    def immediate(function, args=(), result=None, error=None):
        value = function(*args)
        if result:
            result(value)

    monkeypatch.setattr(module, 'background', immediate)
    monkeypatch.setattr(module, 'devices', lambda: ([], ''))
    monkeypatch.setattr(module, 'Recorder', Recorder)
    monkeypatch.setattr(module, 'primary_geometry', lambda: (0, 0, 100, 100))
    settings = QSettings(str(tmp_path / 'settings.ini'), QSettings.Format.IniFormat)
    hotkey = Hotkey()
    window = module.MainWindow(settings=settings, hotkey=hotkey)
    loaded = []

    def open_clip(path, temporary=False):
        window.clip_path = path
        loaded.append(path)
        window.show()

    monkeypatch.setattr(window, '_open_clip', open_clip)
    assert window.editor is None
    assert not window.isVisible()
    assert hotkey.text == 'Ctrl+Shift+R'
    hotkey.activated.emit()
    assert window.recorder.busy and not window.isVisible()
    hotkey.activated.emit()
    assert not window.recorder.busy and window.isVisible()
    assert loaded and loaded[0].name == 'screen.mp4'
    window.close()
    assert not window.isVisible()
    assert window.session.exists()  # Closing returns to the tray; Quit does cleanup.
    assert window.record_action.isEnabled()

    def save_key():
        dialog = app.activeModalWidget()
        dialog.findChild(QKeySequenceEdit).setKeySequence(QKeySequence('Ctrl+Alt+F9'))
        box = dialog.findChild(QDialogButtonBox)
        box.button(QDialogButtonBox.StandardButton.Save).click()

    QTimer.singleShot(0, save_key)
    window._keybind()
    assert settings.value('keybind') == 'Ctrl+Alt+F9'
    assert hotkey.text == 'Ctrl+Alt+F9'
    assert not window.isVisible()
    # A new recording still works after the previous recorder's stopping flag.
    hotkey.activated.emit()
    assert window.recorder.start_count == 2
    window.recorder.stop()
    window.timer.stop()
    window.hide()
    window.tray.hide()
    window.deleteLater()
    app.processEvents()


class FakeEditorProcess(QObject):
    idle = Signal()
    saved = Signal(object)
    closed = Signal()
    failed = Signal(str)
    blocked = Signal()

    def __init__(self, path, temporary, state, parent):
        super().__init__(parent)
        self.state = state
        self.loading = self.exporting = self.modal = False
        self.commands = []

    def send(self, command):
        self.commands.append(command)


def test_tray_waits_for_editor_exit_before_recording_and_reclaims_controller(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])

    def immediate(function, args=(), result=None, error=None):
        value = function(*args)
        if result:
            result(value)

    monkeypatch.setattr(module, 'background', immediate)
    monkeypatch.setattr(module, 'devices', lambda: ([], ''))
    monkeypatch.setattr(module, 'Recorder', Recorder)
    monkeypatch.setattr(module, 'EditorProcess', FakeEditorProcess)
    monkeypatch.setattr(module, 'primary_geometry', lambda: (0, 0, 100, 100))
    window = module.MainWindow(settings=QSettings(str(tmp_path / 'memory.ini'), QSettings.Format.IniFormat), hotkey=Hotkey())
    assert window.editor is None
    window._open_clip(tmp_path / 'clip.mp4')
    editor = window.editor
    assert editor is not None and window.centralWidget() is None
    window._record()
    assert window.record_pending and editor.commands == ['close']
    assert window.recorder.start_count == 0
    editor.closed.emit()
    assert window.editor is None and window.recorder.start_count == 1
    window.recorder.stop()
    assert window.editor is not None
    saved = tmp_path / 'export.mp4'
    window.editor.saved.emit(saved)
    assert saved.resolve() in window.sessions.exports
    window.editor.closed.emit()
    window.timer.stop()
    window.tray.hide()
    window.sessions.cleanup()
    window.deleteLater()
    app.processEvents()


class FakeReplay(QObject):
    started = Signal()
    stopped = Signal()
    changed = Signal()
    ready = Signal(object)
    failed = Signal(str)
    clip_failed = Signal(str)

    def __init__(self, parent):
        super().__init__(parent)
        self.active = self.running = self.stopping = self.manual_active = False
        self.manual_finishing = self.clip_pending = False
        self.available = 30
        self.starts = 0
        self.clips = []
        self.destination = None

    def start(self, *args):
        self.starts += 1
        self.active = self.running = True
        self.started.emit()

    def begin_recording(self, destination):
        self.manual_active = True
        self.destination = destination

    def end_recording(self):
        self.manual_active = False
        self.manual_finishing = True

    def clip(self, seconds, destination, **kwargs):
        self.clips.append((seconds, destination))

    def stop(self, **kwargs):
        self.active = self.running = False
        self.stopped.emit()


def test_manual_capture_and_clipping_share_replay_without_starting_second_capture(tmp_path, monkeypatch):
    from app.editor_state import EditorState
    from app.replay_export import ReplayResult
    from app.video_utils import Media
    app = QApplication.instance() or QApplication([])

    def immediate(function, args=(), result=None, error=None):
        value = function(*args)
        if result:
            result(value)

    monkeypatch.setattr(module, 'background', immediate)
    monkeypatch.setattr(module, 'devices', lambda: ([], ''))
    monkeypatch.setattr(module, 'Recorder', Recorder)
    monkeypatch.setattr(module, 'Replay', FakeReplay)
    monkeypatch.setattr(module, 'GlobalHotkey', lambda parent=None: Hotkey())
    monkeypatch.setattr(module, 'EditorProcess', FakeEditorProcess)
    monkeypatch.setattr(module, 'primary_geometry', lambda: (0, 0, 100, 100))
    monkeypatch.setattr(module, 'clip_destination', lambda: tmp_path / 'auto-clip.mp4')
    window = module.MainWindow(settings=QSettings(str(tmp_path / 'replay.ini'), QSettings.Format.IniFormat), hotkey=Hotkey())
    assert not window.replay_enabled and not window.replay.active
    window._toggle_replay(True)
    window._record()
    assert window.replay.manual_active
    assert window.recorder.start_count == 0
    window._set_length(17)
    window._clip()
    assert window.replay.clips[0][0] == 17
    assert window.replay.manual_active and window.replay.starts == 1
    source = window.replay.clips[0][1]
    media = Media(source, 160, 120, 18, True, '30/1', '30/1', '1/1000', 0, (), False)
    state = EditorState(media, True, 0.5, 17.5, 500, (0, 0, 1, 1), 'mp4', None, True)
    window.replay.ready.emit(ReplayResult(state, 'clip'))
    assert window.editor is None  # Clipping does not interrupt the game/editor.
    assert window.last_replay.state == state
    window._record()
    assert not window.replay.manual_active and window.replay.active
    assert window.replay.starts == 1 and window.recorder.start_count == 0
    window.replay.manual_finishing = False
    window.replay.ready.emit(ReplayResult(state, 'recording'))
    assert window.editor is not None  # Stopping normal recording opens the editor.
    window.editor.closed.emit()
    window._toggle_replay(False)
    assert not window.replay.active and not window.replay_enabled
    window.timer.stop()
    window.tray.hide()
    window.sessions.cleanup()
    window.deleteLater()
    app.processEvents()


