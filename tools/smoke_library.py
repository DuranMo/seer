"""Opt-in native library/editor reuse and process-exit check."""
from pathlib import Path
import sys
import time
import tempfile
import shutil
from multiprocessing import freeze_support

if __name__ == '__main__':
    freeze_support()

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication
from app.editor_process import EditorProcess


def main():
    path = Path(sys.argv[1]).resolve()
    rename = '--rename' in sys.argv
    folder = None
    if rename:
        Path('.tools').mkdir(exist_ok=True)
        folder = Path(tempfile.mkdtemp(prefix='library-rename-', dir='.tools')).resolve()
        shutil.copyfile(path, folder / 'original.mp4')
        path = folder / 'original.mp4'
        from app.clip_storage import save_description
        save_description(path, 'Description remains separate from the filename.')
    app = QApplication([])
    app.setQuitOnLastWindowClosed(False)
    host = EditorProcess(None, False, None, library=folder or True)
    pid = host.process.pid
    stage = 0
    opened = time.monotonic()
    errors = []

    def tick():
        nonlocal stage, opened, path
        if host.loading or host.finished:
            return
        if stage == 0:
            stage = 1
            opened = time.monotonic()
            host.send(('open', path, False, None))
        elif stage == 1 and host.media is not None:
            print(f'Warm editor opened in {time.monotonic() - opened:.3f}s; PID {pid}', flush=True)
            assert host.process.pid == pid
            if rename:
                stage = 5
                host.send(('rename', 'renamed.mp4'))
            else:
                stage = 2
                host.send('editor_close')
                QTimer.singleShot(200, reopen)
        elif stage == 5 and host.media is not None and host.media.path.name == 'renamed.mp4':
            from app.clip_storage import read_description
            assert not path.exists()
            path = host.media.path
            assert path.exists() and read_description(path) == 'Description remains separate from the filename.'
            print('Renamed the active video and sidecar; editor state now uses the new filename.', flush=True)
            stage = 2
            host.send('editor_close')
            QTimer.singleShot(200, reopen)
        elif stage == 3 and host.media is not None:
            assert host.process.pid == pid
            print('Reopened in the same GUI process; closing manager and editor.', flush=True)
            stage = 4
            host.send('editor_close')
            QTimer.singleShot(200, lambda: host.send('library_close'))

    def reopen():
        nonlocal stage
        assert host.process.is_alive()
        stage = 3
        host.send(('open', path, False, None))

    def closed():
        print('GUI process exited; decoder and library released.', flush=True)
        app.exit(int(bool(errors)))

    host.failed.connect(lambda error: (errors.append(error), print(error, flush=True)))
    host.closed.connect(closed)
    timer = QTimer()
    timer.setInterval(50)
    timer.timeout.connect(tick)
    timer.start()
    QTimer.singleShot(20000, lambda: (errors.append('Timed out'), host.send('quit')))
    return app.exec()


if __name__ == '__main__':
    raise SystemExit(main())
