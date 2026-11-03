from __future__ import annotations

from pathlib import Path
import sys
import time

from PySide6.QtCore import QSettings, QTimer
from PySide6.QtGui import QActionGroup, QKeySequence
from PySide6.QtWidgets import (QApplication, QDialog, QDialogButtonBox, QFileDialog,
    QInputDialog, QKeySequenceEdit, QLabel, QMainWindow, QMenu, QSystemTrayIcon, QVBoxLayout)

from .audio import devices
from .clip_storage import clip_destination
from .display import primary_geometry
from .editor_state import EditorState
from .editor_process import EditorProcess
from .exporter import RATES
from .hotkey import DEFAULT_CLIP_KEYBIND, DEFAULT_KEYBIND, GlobalHotkey
from .icons import application_icon
from .jobs import background
from .native_menu import NativeMenu
from .recorder import Recorder
from .replay import Replay
from .sessions import RecordingSessions
from .timing import clock


class MainWindow(QMainWindow):
    """Tray controller; the only top-level window is the small clip editor."""
    def __init__(self, settings=None, hotkey=None):
        super().__init__()
        self.setWindowTitle('Seer')
        self.setWindowIcon(application_icon())
        self.resize(600, 440)
        self.setMinimumSize(460, 360)
        self.settings = settings if settings is not None else QSettings()
        self.hotkey = hotkey if hotkey is not None else GlobalHotkey(self)
        self.hotkey.activated.connect(self._record)
        self.clip_hotkey = GlobalHotkey(self)
        self.clip_hotkey.activated.connect(self._clip)
        self.replay = Replay(self)
        self.replay.started.connect(self._replay_started)
        self.replay.stopped.connect(self._replay_stopped)
        self.replay.changed.connect(self._sync)
        self.replay.ready.connect(self._replay_ready)
        self.replay.failed.connect(self._replay_failed)
        self.replay.clip_failed.connect(self._clip_failed)
        self.replay_enabled = False
        self.last_replay = None
        length = int(self.settings.value('replay/seconds', 30))
        self.clip_seconds = max(1, min(120, length))
        self.quitting = self.configuring_keybind = False
        self.session = None
        self.sessions = RecordingSessions()
        self.cleaning_up = False
        self.started_at = 0.0
        self.recording = False
        self.audio_devices = []
        self.audio_loading = False
        self.editor = None
        self.clip_path: Path | None = None
        self.clip_temporary = False
        self.clip_state: EditorState | None = None
        self.record_pending = False
        self.pending_clip = None
        self.recorder = Recorder(self)
        self.recorder.started.connect(self._started)
        self.recorder.ready.connect(self._recorded)
        self.recorder.failed.connect(self._failed)
        self.recorder.status.connect(self._capture_status)
        self.menu = QMenu()
        self.record_action = self.menu.addAction('Start recording', self._record)
        self.keybind_action = self.menu.addAction('Keybind…', self._keybind)
        self.capture_menu = self.menu.addMenu('Recording')
        rate_menu = self.capture_menu.addMenu('Frame rate')
        self.rate_group = QActionGroup(self)
        saved_rate = int(self.settings.value('recording/fps', 30))
        self.fps = saved_rate if saved_rate in RATES else 30
        for rate in RATES:
            action = rate_menu.addAction(f'{rate} FPS')
            action.setCheckable(True)
            action.setData(rate)
            action.setChecked(rate == self.fps)
            self.rate_group.addAction(action)
        self.rate_group.triggered.connect(self._rate_selected)
        self.audio_menu = self.capture_menu.addMenu('Audio')
        self.audio_group = QActionGroup(self)
        self.audio_group.triggered.connect(self._audio_selected)
        replay_menu = self.menu.addMenu('Replay')
        self.replay_action = replay_menu.addAction('Enabled')
        self.replay_action.setCheckable(True)
        self.replay_action.triggered.connect(self._toggle_replay)
        self.clip_action = replay_menu.addAction('Clip last 30 seconds', self._clip)
        self.length_menu = replay_menu.addMenu('Clip length')
        self.length_group = QActionGroup(self)
        for seconds in (15, 30, 60, 120):
            action = self.length_menu.addAction(f'{seconds} seconds')
            action.setCheckable(True)
            action.setData(seconds)
            action.setChecked(seconds == self.clip_seconds)
            self.length_group.addAction(action)
        self.length_group.triggered.connect(self._length_selected)
        self.length_menu.addAction('Custom…', self._custom_length)
        self.clip_keybind_action = replay_menu.addAction('Clip keybind…', lambda: self._keybind(True))
        self.last_clip_action = replay_menu.addAction('Open last clip', self._open_last_clip)
        self.menu.addSeparator()
        self.open_action = self.menu.addAction('Open video…', self._open)
        self.editor_action = self.menu.addAction('Show editor', self._show_editor)
        self.editor_action.setEnabled(False)
        self.menu.addSeparator()
        self.menu.addAction('Quit', self._request_quit)
        self.tray = QSystemTrayIcon(self.windowIcon(), self)
        self.native_menu = NativeMenu() if sys.platform == 'win32' else None
        if self.native_menu is None:
            self.tray.setContextMenu(self.menu)
        else:
            self.tray.activated.connect(self._tray_activated)
        if QSystemTrayIcon.isSystemTrayAvailable():
            self.tray.show()
        self.timer = QTimer(self)
        self.timer.setInterval(500)
        self.timer.timeout.connect(self._tick)
        self.binding_error = ''
        preferred = str(self.settings.value('keybind', DEFAULT_KEYBIND))
        try:
            self.hotkey.set_shortcut(preferred)
        except (RuntimeError, ValueError) as error:
            self.binding_error = str(error)
            if preferred != DEFAULT_KEYBIND:
                try:
                    self.hotkey.set_shortcut(DEFAULT_KEYBIND)
                except (RuntimeError, ValueError):
                    pass
            QTimer.singleShot(0, self._binding_warning)
        self._sync()
        self._refresh_audio()
        QApplication.instance().aboutToQuit.connect(self.hotkey.close)
        QApplication.instance().aboutToQuit.connect(self.clip_hotkey.close)

    def _tray_activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.Context:
            try:
                self.native_menu.show(self.menu, self)
            except RuntimeError as error:
                self._notify(str(error), True)

    def _notify(self, message, error=False):
        icon = QSystemTrayIcon.MessageIcon.Warning if error else QSystemTrayIcon.MessageIcon.Information
        self.tray.showMessage('Seer', message, icon, 4000)

    def _binding_warning(self):
        self._notify(self.binding_error + ' Right-click Seer → Keybind to change it.', True)

    def _sync(self):
        if not hasattr(self, 'replay_action'):
            return
        busy = self.recorder.busy or self.replay.manual_active
        shortcut = self.hotkey.text or 'no keybind'
        self.record_action.setText(('Stop recording' if busy else 'Start recording') + f' ({shortcut})')
        self.record_action.setEnabled(not self.quitting and not (self.recorder.busy and self.recorder.stopping)
                                     and (busy or not self.editor_busy) and not self.replay.manual_finishing
                                     and (not self.replay.active or self.replay.running))
        self.capture_menu.setEnabled(not busy and not self.replay.active and not self.quitting)
        self.keybind_action.setEnabled(not busy and not self.quitting)
        self.open_action.setEnabled(not busy and not self.editor_busy and not self.quitting)
        self.editor_action.setEnabled(self.clip_path is not None and not busy and not self.quitting)
        clip_shortcut = self.clip_hotkey.text or str(self.settings.value('replay/keybind', DEFAULT_CLIP_KEYBIND))
        self.clip_action.setText(f'Clip last {self.clip_seconds}s ({clip_shortcut})')
        self.clip_action.setEnabled(self.replay_enabled and self.replay.running and not self.replay.clip_pending and not self.quitting)
        self.replay_action.setEnabled(not self.recorder.busy and not self.replay.manual_active
                                      and not self.replay.manual_finishing and not self.replay.clip_pending and not self.quitting
                                      and not self.replay.stopping)
        self.last_clip_action.setEnabled(self.last_replay is not None and not self.editor_busy and not busy and not self.quitting)
        self.clip_keybind_action.setEnabled(not self.quitting)
        if not busy and not self.replay.active:
            self.tray.setToolTip(f'Seer · {shortcut} to record')

    def _capture_status(self, message):
        self.tray.setToolTip('Seer · ' + message)
        self._sync()

    def _rate_selected(self, action):
        self.fps = action.data()
        self.settings.setValue('recording/fps', self.fps)

    def _refresh_audio(self):
        if self.audio_loading:
            return
        self.audio_loading = True
        self.audio_worker = background(devices, result=self._audio_devices, error=self._audio_error)

    def _audio_devices(self, result):
        self.audio_loading = False
        self.audio_devices, message = result
        for action in self.audio_group.actions():
            self.audio_group.removeAction(action)
        self.audio_menu.clear()
        selected = str(self.settings.value('recording/audio', ''))
        silent = self.audio_menu.addAction('No audio')
        silent.setCheckable(True)
        silent.setData('')
        self.audio_group.addAction(silent)
        matched = False
        for device in self.audio_devices:
            identifier = device.mode + ':' + device.name
            action = self.audio_menu.addAction(('System · ' if device.mode == 'system' else 'Microphone · ') + device.name)
            action.setCheckable(True)
            action.setData(identifier)
            action.setChecked(identifier == selected)
            matched |= identifier == selected
            self.audio_group.addAction(action)
        silent.setChecked(not selected)
        if selected and not matched:
            missing = self.audio_menu.addAction('Selected device unavailable')
            missing.setEnabled(False)
        if message:
            note = self.audio_menu.addAction('Audio unavailable')
            note.setToolTip(message)
            note.setEnabled(False)
        self.audio_menu.addSeparator()
        self.audio_menu.addAction('Refresh devices', self._refresh_audio)
        self._maybe_quit()

    def _audio_error(self, message):
        self._audio_devices(([], message))

    def _audio_selected(self, action):
        self.settings.setValue('recording/audio', action.data())

    def _selected_audio(self):
        selected = str(self.settings.value('recording/audio', ''))
        device = next((d for d in self.audio_devices if d.mode + ':' + d.name == selected), None)
        if selected and (self.audio_loading or device is None):
            raise RuntimeError('The selected audio device is unavailable. Choose Audio in the tray menu or refresh devices.')
        return device

    def _toggle_replay(self, enabled):
        if self.quitting:
            return
        if enabled:
            if self.recorder.busy or self.replay.active:
                self.replay_action.setChecked(self.replay_enabled)
                return
            try:
                device = self._selected_audio()
                self.clip_hotkey.set_shortcut(str(self.settings.value('replay/keybind', DEFAULT_CLIP_KEYBIND)))
                self.replay_enabled = True
                self.replay_action.setChecked(True)
                self.replay.start(self.fps, device, primary_geometry())
                self.timer.start()
            except Exception as error:
                self._replay_failed(str(error))
        else:
            if self.replay.manual_active or self.replay.manual_finishing or self.replay.clip_pending:
                self.replay_action.setChecked(True)
                return
            self.replay_enabled = False
            self.replay_action.setChecked(False)
            self.clip_hotkey.unregister()
            self.replay.stop()
        self._sync()

    def _replay_started(self):
        self.timer.start()
        self._tick()
        self._sync()

    def _replay_stopped(self):
        if not self.recorder.busy:
            self.timer.stop()
        self._maybe_quit()

    def _replay_failed(self, message):
        self.replay_enabled = False
        self.recording = False
        self.replay_action.setChecked(False)
        self.clip_hotkey.unregister()
        self._notify('Replay stopped: ' + message[-1800:], True)
        self._sync()

    def _clip_failed(self, message):
        self._notify(message, True)
        self._sync()

    def _clip(self):
        if self.quitting or self.configuring_keybind:
            return
        if not self.replay_enabled:
            self._notify('Enable Replay in the tray menu first.')
            return
        if not self.replay.running or self.replay.clip_pending:
            self._notify('Replay is starting or the previous clip is still finishing. Try again in a moment.')
            return
        try:
            self.replay.clip(self.clip_seconds, clip_destination(), permanent=True)
        except Exception as error:
            self._notify(str(error), True)

    def _set_length(self, seconds):
        self.clip_seconds = seconds
        self.settings.setValue('replay/seconds', seconds)
        for action in self.length_group.actions():
            action.setChecked(action.data() == seconds)
        self._sync()

    def _length_selected(self, action):
        self._set_length(action.data())

    def _custom_length(self):
        seconds, accepted = QInputDialog.getInt(self, 'Clip length', 'Seconds (1–120)', self.clip_seconds, 1, 120)
        if accepted:
            self._set_length(seconds)

    def _replay_ready(self, result):
        if self.quitting:
            return
        if result.kind == 'clip':
            self.last_replay = result
            if self.editor is None:
                self.clip_path = result.state.media.path
                self.clip_state = result.state
                self.clip_temporary = result.state.temporary_source
            duration = result.state.end - result.state.start
            self._notify(f'Saved {duration:.1f}s clip to {result.state.media.path}')
        else:
            self._open_result(result)
        self._sync()

    def _open_result(self, result):
        self._open_clip(result.state.media.path, temporary=result.state.temporary_source, state=result.state)

    def _open_last_clip(self):
        if self.last_replay is not None:
            self._open_result(self.last_replay)

    def _keybind(self, clipping=False):
        if self.configuring_keybind or self.recorder.busy:
            return
        self.configuring_keybind = True
        dialog = QDialog(self)
        hotkey = self.clip_hotkey if clipping else self.hotkey
        setting = 'replay/keybind' if clipping else 'keybind'
        default = DEFAULT_CLIP_KEYBIND if clipping else DEFAULT_KEYBIND
        dialog.setWindowTitle('Clip keybind' if clipping else 'Keybind')
        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel('Save the most recent clip.' if clipping else 'Press once to record, again to stop.'))
        key = QKeySequenceEdit(QKeySequence(hotkey.text or str(self.settings.value(setting, default))))
        key.setMaximumSequenceLength(1)
        layout.addWidget(key)
        error_label = QLabel('')
        error_label.setWordWrap(True)
        layout.addWidget(error_label)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        layout.addWidget(buttons)

        def save():
            try:
                text = key.keySequence().toString(QKeySequence.SequenceFormat.PortableText)
                other = self.hotkey.text if clipping else (self.clip_hotkey.text or str(self.settings.value('replay/keybind', DEFAULT_CLIP_KEYBIND)))
                if QKeySequence(text) == QKeySequence(other):
                    raise ValueError('Recording and clipping need different shortcuts.')
                hotkey.set_shortcut(text)
                self.settings.setValue(setting, hotkey.text)
                if clipping and not self.replay_enabled:
                    hotkey.unregister()
                self.settings.sync()
                self._sync()
                dialog.accept()
            except (RuntimeError, ValueError) as error:
                error_label.setText(str(error))

        buttons.accepted.connect(save)
        buttons.rejected.connect(dialog.reject)
        try:
            dialog.exec()
        finally:
            self.configuring_keybind = False
            dialog.deleteLater()

    def _record(self):
        if self.quitting or self.configuring_keybind:
            return
        if self.replay.manual_active:
            self.replay.end_recording()
            self.recording = False
            self._sync()
            return
        if self.recorder.busy:
            self.recorder.stop()
            self._sync()
            return
        if self.replay.active and not self.replay.running:
            self._notify('Replay is starting or finishing. Wait a moment before recording.')
            return
        if self.editor_busy or QApplication.activeModalWidget() is not None:
            self._notify('Finish opening, saving, or changing options first.')
            return
        try:
            selected = str(self.settings.value('recording/audio', ''))
            device = next((d for d in self.audio_devices if d.mode + ':' + d.name == selected), None)
            if selected and (self.audio_loading or device is None):
                raise RuntimeError('The selected audio device is unavailable. Choose Audio in the tray menu or refresh devices.')
            if self.editor is not None:
                self.pending_clip = None
                self.record_pending = True
                self.editor.send('close')
                return
            self.hide()
            if self.replay_enabled:
                self.session = self.sessions.create()
                self.replay.begin_recording(self.session / 'screen.mp4')
                self.recording = True
                self.started_at = time.monotonic()
                self.timer.start()
                self._sync()
                return
            self.session = self.sessions.create()
            self.recorder.start(self.session, self.fps, device, primary_geometry())
            self._sync()
        except Exception as error:
            self._failed(str(error))

    def _started(self):
        self.recording = True
        self.started_at = time.monotonic()
        self.timer.start()
        self._tick()
        self._sync()

    def _tick(self):
        if self.replay.active:
            if self.replay.manual_active:
                text = f'Recording {clock(time.monotonic() - self.started_at)} · Replay on'
            elif self.replay.running:
                text = f'Replay on · {self.replay.available:.0f}s buffered · {self.clip_hotkey.text} to clip'
            else:
                text = 'Finishing replay…' if self.replay.stopping else 'Starting replay…'
            self.tray.setToolTip('Seer · ' + text)
            return
        self.tray.setToolTip(f'Seer · Recording {clock(time.monotonic() - self.started_at)} · {self.hotkey.text} to stop')
        if self.recorder.audio and self.recorder.audio.error:
            self.recorder.stop()

    def _reset(self):
        self.recording = False
        self.timer.stop()
        self._sync()

    def _recorded(self, path):
        self._reset()
        if self.quitting:
            self._maybe_quit()
            return
        self._open_clip(path, temporary=True)
        self._sync()

    def _failed(self, message):
        self._reset()
        if self.session:
            message += '\nFiles retained in ' + str(self.session)
        self._notify(message[-2000:], True)
        self._maybe_quit()

    def _open(self):
        if self.recorder.busy or self.editor_busy:
            return
        # An asynchronous native dialog keeps replay's segment monitor running.
        self.open_dialog = QFileDialog(self, 'Open video')
        self.open_dialog.setFileMode(QFileDialog.FileMode.ExistingFile)
        self.open_dialog.setNameFilters(['Video (*.mp4 *.mkv *.mov *.webm *.avi *.m4v)', 'All files (*)'])
        self.open_dialog.fileSelected.connect(self._open_selected)
        self.open_dialog.finished.connect(self.open_dialog.deleteLater)
        self.open_dialog.open()

    def _open_selected(self, name):
        self._open_clip(Path(name))
        self._sync()

    @property
    def editor_busy(self):
        return bool(self.editor and (self.editor.loading or self.editor.exporting or self.editor.modal))

    def _open_clip(self, path: Path, temporary=False, state=None):
        if self.editor is not None:
            self.record_pending = False
            self.pending_clip = (path, temporary, state)
            if not self.editor_busy:
                self.editor.send('close')
            return
        self.clip_path = path.resolve()
        self.clip_temporary = temporary
        self.clip_state = state
        self._show_editor()

    def _show_editor(self):
        if self.clip_path is None:
            return
        if self.editor is not None:
            self.editor.send('show')
        else:
            try:
                self.editor = EditorProcess(self.clip_path, self.clip_temporary, self.clip_state, self)
                self.editor.idle.connect(self._editor_idle)
                self.editor.saved.connect(self.sessions.protect_export)
                self.editor.closed.connect(self._editor_closed)
                self.editor.failed.connect(self._editor_failed)
                self.editor.blocked.connect(self._editor_blocked)
            except Exception as error:
                self._notify('Could not open the editor: ' + str(error), True)
        self._sync()

    def _editor_idle(self):
        if self.pending_clip is not None and not self.editor_busy and not self.quitting:
            self.editor.send('close')
        self._maybe_quit()

    def _editor_closed(self):
        self.clip_state = self.editor.state
        self.editor.deleteLater()
        self.editor = None
        if self.pending_clip is not None and not self.quitting:
            path, temporary, state = self.pending_clip
            self.pending_clip = None
            self._open_clip(path, temporary, state)
        elif self.record_pending:
            self.record_pending = False
            self._record()
        self._maybe_quit()

    def _editor_failed(self, message):
        self._notify(message, True)

    def _editor_blocked(self):
        self.record_pending = False
        self.pending_clip = None
        self._notify('Finish saving or changing options in the editor first.')

    def _request_quit(self):
        self.quitting = True
        self._sync()
        if self.recorder.busy:
            self.recorder.stop()
        if self.replay.active:
            self.replay_enabled = False
            self.replay.stop(discard=True)
        self.clip_hotkey.unregister()
        self.record_pending = False
        self.pending_clip = None
        if self.editor is not None:
            self.editor.send('quit')
        self._maybe_quit()

    def _maybe_quit(self):
        self._sync()
        if self.quitting and not self.cleaning_up and not (self.recorder.busy or self.replay.active or self.editor is not None or self.audio_loading):
            self.cleaning_up = True
            self.timer.stop()
            self.hotkey.close()
            self.settings.sync()
            self.hide()
            self.tray.setToolTip('Seer · Removing temporary recordings…')
            # Give the decoder a turn to release its Windows file handles.
            QTimer.singleShot(100, self._cleanup_sessions)

    def _cleanup_sessions(self):
        self.cleanup_worker = background(self.sessions.cleanup, result=self._cleanup_finished,
                                         error=self._cleanup_failed)

    def _cleanup_finished(self, failed):
        if failed:
            self._notify('Windows could not remove some temporary files: ' + str(failed[0]), True)
        self.tray.hide()
        QApplication.instance().quit()

    def _cleanup_failed(self, message):
        self._notify('Temporary recording cleanup failed: ' + message, True)
        self.tray.hide()
        QApplication.instance().quit()

    def closeEvent(self, event):
        self.hide()
        if self.editor is not None:
            self.editor.send('close')
        event.ignore()  # Closing the editor returns to the tray; Quit is explicit.
