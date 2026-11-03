"""Shared GUI process, released when both the manager and editor close."""
from __future__ import annotations

import multiprocessing
from pathlib import Path
import threading

from PySide6.QtCore import QObject, QTimer, Qt, Signal

from .editor_state import EditorState


class EditorProcess(QObject):
    idle = Signal()
    saved = Signal(object)
    closed = Signal()
    failed = Signal(str)
    blocked = Signal()
    state_changed = Signal(object)
    renamed = Signal(object, object)
    deleted = Signal(object)

    def __init__(self, path: Path | None, temporary: bool, state: EditorState | None, parent=None, *, library=False):
        super().__init__(parent)
        self.state = state
        self.loading, self.exporting, self.modal = True, False, False
        self.finished = False
        context = multiprocessing.get_context('spawn')
        self.connection, child = context.Pipe()
        self.process = context.Process(target=run_editor, args=(child, path, temporary, state, library), name='Seer editor')
        try:
            self.process.start()
        except Exception:
            self.connection.close()
            raise
        finally:
            child.close()
        self.timer = QTimer(self)
        self.timer.setInterval(50)
        self.timer.timeout.connect(self._poll)
        self.timer.start()

    @property
    def media(self):
        return self.state.media if self.state else None

    def send(self, command: str | tuple):
        if not self.finished:
            try:
                self.connection.send(command)
            except (BrokenPipeError, EOFError, OSError):
                pass  # Process completion is handled by _poll.

    def _messages(self):
        try:
            while self.connection.poll():
                message = self.connection.recv()
                event = message[0]
                if event == 'state':
                    self.state = message[1]
                    self.state_changed.emit(self.state)
                elif event == 'status':
                    self.loading, self.exporting, self.modal = message[1:]
                    if not self.loading and not self.exporting:
                        self.idle.emit()
                elif event == 'saved':
                    self.saved.emit(message[1])
                elif event == 'renamed':
                    self.renamed.emit(message[1], message[2])
                elif event == 'deleted':
                    if self.state is not None and self.state.media.path == message[1]:
                        self.state = None
                    self.deleted.emit(message[1])
                elif event == 'blocked':
                    self.blocked.emit()
                elif event == 'error':
                    self.failed.emit(message[1])
        except (EOFError, OSError):
            pass

    def _poll(self):
        self._messages()
        if not self.process.is_alive():
            self.process.join()
            self._messages()
            code = self.process.exitcode
            self.process.close()
            self.connection.close()
            self.finished = True
            self.timer.stop()
            self.loading = self.exporting = self.modal = False
            if code:
                self.failed.emit('The editor stopped unexpectedly. The source recording is still available to reopen.')
            self.closed.emit()


def run_editor(connection, path: Path | None, temporary: bool, state: EditorState | None, library_mode=False):
    # Keep all Multimedia imports out of the tray process, including on Windows spawn.
    from PySide6.QtWidgets import QApplication, QMainWindow
    from .editor_window import Editor
    from .icons import application_icon
    from .styles import apply_colors

    app = QApplication([])
    app.setApplicationName('Seer')
    app.setOrganizationName('Seer')
    apply_colors(app)
    app.setQuitOnLastWindowClosed(False)
    app.setWindowIcon(application_icon())
    editor = Editor()
    closing = False
    last_status = None
    library = None
    force_exit = False

    def send(*message):
        try:
            connection.send(message)
        except (BrokenPipeError, EOFError, OSError):
            app.quit()

    def finish():
        send('state', editor.snapshot())
        editor.release_playback()
        app.quit()

    class Window(QMainWindow):
        def closeEvent(self, event):
            nonlocal closing
            closing = force_exit or library is None
            self.hide()
            editor.player.pause()
            send('state', editor.snapshot())
            if closing and not (editor.loading or editor.exporting):
                finish()
            event.ignore()

    window = Window()
    window.setWindowTitle('Seer')
    window.resize(600, 440)
    window.setMinimumSize(460, 360)
    window.setCentralWidget(editor)

    def open_media(path, temporary=False, state=None):
        nonlocal closing
        if editor.loading or editor.exporting or app.activeModalWidget() is not None:
            send('blocked')
            return
        closing = False
        if state is not None:
            editor.restore(state)
        else:
            editor.load(path, temporary=temporary)
        window.showNormal()
        window.raise_()
        window.activateWindow()

    def library_closed(*_):
        nonlocal library, closing
        library = None
        if not window.isVisible():
            closing = True
            if not editor.loading and not editor.exporting:
                finish()

    def show_library():
        nonlocal library
        if library is None:
            from .clip_library import ClipLibrary
            from .clip_storage import clips_folder
            library = ClipLibrary(library_mode if isinstance(library_mode, Path) else clips_folder())
            library.rename_handler = rename_from_library
            library.delete_handler = delete_from_library
            library.open_clip.connect(open_media)
            library.destroyed.connect(library_closed)
        library.showNormal()
        library.raise_()
        library.activateWindow()

    def rename_from_library(old, filename):
        from dataclasses import replace
        from PySide6.QtCore import QUrl
        from .clip_storage import rename_clip
        active = editor.media is not None and editor.media.path == old
        snapshot = None
        if active:
            if editor.loading or editor.exporting or app.activeModalWidget() is not None:
                raise OSError('Finish opening or exporting this clip before renaming it.')
            snapshot = editor.snapshot()
            editor._stop_timing_probe()
            editor.player.stop()
            editor.player.setSource(QUrl())
        try:
            renamed = rename_clip(old, filename)
        except (OSError, ValueError):
            if snapshot is not None:
                editor.restore(snapshot)
            raise
        if snapshot is not None:
            editor.restore(replace(snapshot, media=replace(snapshot.media, path=renamed)))
        send('renamed', old, renamed)
        return renamed

    def delete_from_library(path):
        active = editor.media is not None and editor.media.path == path
        warnings = editor.delete_source(path)
        if active:
            window.hide()
            send('state', None)
        send('deleted', path)
        return warnings

    def idle():
        send('state', editor.snapshot())
        if closing:
            finish()

    def command_received(command):
        nonlocal closing, force_exit
        if isinstance(command, tuple) and command[0] == 'open':
            open_media(*command[1:])
            return
        if isinstance(command, tuple) and command[0] == 'clip_saved':
            if library is not None:
                library.refresh()
            return
        if isinstance(command, tuple) and command[0] == 'rename':
            if library is not None:
                library.title.setText(command[1])
                if not library.save():
                    send('error', library.message.text())
            return
        if command == 'library':
            show_library()
            return
        if command == 'editor_close':
            window.close()
            return
        if command == 'library_close':
            if library is not None:
                library.close()
            return
        if command == 'show':
            closing = False
            window.showNormal()
            window.raise_()
            window.activateWindow()
        elif command in ('close', 'quit'):
            modal = app.activeModalWidget()
            if command == 'close' and (modal is not None or editor.loading or editor.exporting):
                send('blocked')
                return
            if command == 'quit':
                if modal is not None:
                    modal.reject()
                if editor.exporting:
                    editor.job.cancel()
            if library is not None:
                if not library.save():
                    send('error', 'Could not save the clip description. Resolve it before closing.')
                    send('blocked')
                    return
                library.close()
            force_exit = True
            window.close()
        elif command == 'play':
            editor.audio.setMuted(True)
            editor._toggle()

    def disconnected():
        nonlocal closing, force_exit
        force_exit = True
        closing = True
        if library is not None:
            library.close()
        modal = app.activeModalWidget()
        if modal is not None:
            modal.reject()
        if editor.exporting:
            editor.job.cancel()
        if not editor.loading and not editor.exporting:
            finish()

    class Commands(QObject):
        received = Signal(object)
        disconnected = Signal()

    commands = Commands()
    commands.received.connect(command_received, Qt.ConnectionType.QueuedConnection)
    commands.disconnected.connect(disconnected, Qt.ConnectionType.QueuedConnection)
    reader_stopped = threading.Event()

    def receive():
        try:
            while not reader_stopped.is_set():
                command = connection.recv()
                commands.received.emit(command)
        except (EOFError, OSError):
            if not reader_stopped.is_set():
                commands.disconnected.emit()

    def poll():
        nonlocal closing, last_status
        status = (editor.loading, editor.exporting, app.activeModalWidget() is not None)
        if status != last_status:
            last_status = status
            send('status', *status)

    editor.saved.connect(lambda destination: send('saved', destination))
    editor.idle.connect(idle)
    timer = QTimer()
    timer.setInterval(50)
    timer.timeout.connect(poll)
    if library_mode:
        show_library()
    elif path is not None:
        open_media(path, temporary, state)
    timer.start()
    # A blocking pipe reader can deliver commands even inside native file dialogs,
    # whose modal loops do not dispatch Qt timers. No reader exists in the tray.
    threading.Thread(target=receive, daemon=True, name='Seer editor commands').start()
    try:
        app.exec()
    finally:
        reader_stopped.set()
        connection.close()
