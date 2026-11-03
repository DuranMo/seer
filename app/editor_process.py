"""On-demand editor process. Closing it releases decoder and driver allocations."""
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

    def __init__(self, path: Path, temporary: bool, state: EditorState | None, parent=None):
        super().__init__(parent)
        self.state = state
        self.loading, self.exporting, self.modal = True, False, False
        self.finished = False
        context = multiprocessing.get_context('spawn')
        self.connection, child = context.Pipe()
        self.process = context.Process(target=run_editor, args=(child, path, temporary, state), name='Seer editor')
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

    def send(self, command: str):
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
                elif event == 'status':
                    self.loading, self.exporting, self.modal = message[1:]
                    if not self.loading and not self.exporting:
                        self.idle.emit()
                elif event == 'saved':
                    self.saved.emit(message[1])
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


def run_editor(connection, path: Path, temporary: bool, state: EditorState | None):
    # Keep all Multimedia imports out of the tray process, including on Windows spawn.
    from PySide6.QtWidgets import QApplication, QMainWindow
    from .editor_window import Editor
    from .icons import application_icon
    from .styles import apply_colors

    app = QApplication([])
    apply_colors(app)
    app.setQuitOnLastWindowClosed(False)
    app.setWindowIcon(application_icon())
    editor = Editor()
    closing = False
    last_status = None

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
            closing = True
            self.hide()
            editor.player.pause()
            if not (editor.loading or editor.exporting):
                finish()
            event.ignore()

    window = Window()
    window.setWindowTitle('Seer')
    window.resize(600, 440)
    window.setMinimumSize(460, 360)
    window.setCentralWidget(editor)

    def idle():
        send('state', editor.snapshot())
        if closing:
            finish()

    def command_received(command):
        nonlocal closing
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
            window.close()
        elif command == 'play':
            editor.audio.setMuted(True)
            editor._toggle()
        elif command == 'snapshot':
            send('state', editor.snapshot())

    def disconnected():
        nonlocal closing
        closing = True
        modal = app.activeModalWidget()
        if modal is not None:
            modal.reject()
        if editor.exporting:
            editor.job.cancel()
        if not editor.loading and not editor.exporting:
            finish()

    class Commands(QObject):
        received = Signal(str)
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
    window.show()
    if state is not None:
        editor.restore(state)
    else:
        editor.load(path, temporary=temporary)
    timer.start()
    # A blocking pipe reader can deliver commands even inside native file dialogs,
    # whose modal loops do not dispatch Qt timers. No reader exists in the tray.
    threading.Thread(target=receive, daemon=True, name='Seer editor commands').start()
    try:
        app.exec()
    finally:
        reader_stopped.set()
        connection.close()
