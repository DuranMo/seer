"""Native shared capture/replay/editor-process workflow; temporary output only."""
from __future__ import annotations

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import QObject, QSettings, QTimer, Signal
from PySide6.QtWidgets import QApplication

from app.audio import devices
from app.main_window import MainWindow
from app.styles import apply_colors


def main():
    class Hotkey(QObject):
        activated = Signal()
        text = 'Ctrl+Shift+R'

        def set_shortcut(self, text):
            self.text = text

        def close(self):
            pass

    app = QApplication([])
    app.setQuitOnLastWindowClosed(False)
    apply_colors(app)
    settings = QSettings(str(Path('.tools/replay-ui.ini').resolve()), QSettings.Format.IniFormat)
    settings.setValue('replay/keybind', 'Ctrl+Alt+Shift+F9')
    settings.setValue('replay/seconds', 2)
    import pyaudiowpatch as pa
    with pa.PyAudio() as backend:
        index = backend.get_default_wasapi_loopback()['index']
    device = next(d for d in devices()[0] if d.index == index)
    settings.setValue('recording/audio', device.mode + ':' + device.name)
    window = MainWindow(settings=settings, hotkey=Hotkey())
    paths = []
    errors = []
    passed = False
    opening = False
    pid = None
    buffer = None

    def arm():
        if window.audio_loading:
            QTimer.singleShot(50, arm)
            return
        window._toggle_replay(True)

    def started():
        nonlocal pid, buffer
        pid, buffer = window.replay.process.processId(), window.replay.folder
        window._record()
        QTimer.singleShot(1500, window._clip)
        QTimer.singleShot(3500, window._record)

    def ready(result):
        paths.append((result.state.media.path, result.kind))
        assert window.replay.running and window.replay.process.processId() == pid
        if result.kind == 'clip':
            assert window.editor is None
            print('Replay clip completed without interrupting the shared capture stream.', flush=True)
        else:
            window.editor.idle.connect(editor_ready)

    def editor_ready():
        nonlocal opening
        if opening or window.editor.loading:
            return
        opening = True
        assert window.replay.running and window.editor.media.has_audio
        assert 'PySide6.QtMultimedia' not in sys.modules
        print('Manual recording opened in the editor while replay continued.', flush=True)
        QTimer.singleShot(500, close_editor)

    def close_editor():
        window.close()
        QTimer.singleShot(100, editor_closed)

    def editor_closed():
        nonlocal passed
        if window.editor is not None:
            QTimer.singleShot(100, editor_closed)
            return
        assert window.replay.running and window.replay.process.processId() == pid
        passed = True
        window._toggle_replay(False)

    def stopped():
        window._request_quit()

    def failed(message):
        errors.append(message)
        print('FAILED:', message, flush=True)
        window._request_quit()

    window.replay.started.connect(started)
    window.replay.ready.connect(ready)
    window.replay.stopped.connect(stopped)
    window.replay.failed.connect(failed)
    window.replay.clip_failed.connect(failed)
    previous_hook = sys.excepthook

    def unhandled(kind, error, traceback):
        previous_hook(kind, error, traceback)
        failed(str(error))

    sys.excepthook = unhandled
    QTimer.singleShot(0, arm)
    QTimer.singleShot(35000, lambda: failed('UI replay test timed out.'))
    app.exec()
    sys.excepthook = previous_hook
    assert buffer is not None and not buffer.exists()
    for path, kind in paths:
        if kind == 'clip':
            assert path.exists(), 'Automatically saved clips should survive Quit.'
            assert path.parent.name == 'Seer' and path.name.startswith('Clip-')
            path.unlink()  # Remove only this tool's uniquely named test clip.
        else:
            assert not path.exists()
    assert passed and not errors
    print('OK: shared capture, automatic clip saving, editor, and cleanup all passed.', flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
