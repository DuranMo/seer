"""Connect optional native WGC frames directly to FFmpeg, outside Python RAM."""
from PySide6.QtCore import QObject, QProcess, QTimer, Signal
from .video_utils import executable
from .capture_sources import validate


class WindowVideo(QObject):
    failed = Signal(str)

    def __init__(self, encoder, parent=None):
        super().__init__(parent)
        self.process = QProcess(self)
        self.process.setStandardOutputProcess(encoder)
        self.process.finished.connect(self._finished)
        self.process.errorOccurred.connect(self._error)
        self.stopping = False
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.process.kill)

    def start(self, fps, geometry):
        validate(geometry.windows)
        self.stopping = False
        self.process.start(executable('seer-capture'), ['video', str(fps), str(geometry[2]),
            str(geometry[3])] + [str(w.handle) for w in geometry.windows])

    def stop(self):
        self.stopping = True
        if self.process.state() != QProcess.ProcessState.NotRunning:
            self.process.write(b'q\n')
            self.process.closeWriteChannel()
            self.timer.start(1500)

    def kill(self):
        self.stopping = True
        self.process.kill()
        self.process.waitForFinished(1000)

    def _error(self, error):
        if error == QProcess.ProcessError.FailedToStart and not self.stopping:
            self.failed.emit('Could not start Windows capture: ' + self.process.errorString())

    def _finished(self, code, *_):
        self.timer.stop()
        message = bytes(self.process.readAllStandardError()).decode('utf-8', 'replace').strip()
        if not self.stopping:
            self.failed.emit(message or 'Selected-window capture stopped unexpectedly.')
