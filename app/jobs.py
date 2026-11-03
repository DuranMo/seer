from __future__ import annotations

from PySide6.QtCore import QObject, QProcess, QRunnable, QThreadPool, Signal

from .video_utils import executable


class WorkerSignals(QObject):
    result = Signal(object)
    error = Signal(str)
    finished = Signal()


class Worker(QRunnable):
    def __init__(self, function, *args):
        super().__init__()
        self.function, self.args = function, args
        self.signals = WorkerSignals()

    def run(self):
        try:
            self.signals.result.emit(self.function(*self.args))
        except Exception as error:
            self.signals.error.emit(str(error))
        finally:
            self.signals.finished.emit()


def background(function, args=(), result=None, error=None, finished=None):
    worker = Worker(function, *args)
    if result:
        worker.signals.result.connect(result)
    if error:
        worker.signals.error.connect(error)
    if finished:
        worker.signals.finished.connect(finished)
    QThreadPool.globalInstance().start(worker)
    return worker


class FFmpegJob(QObject):
    progress = Signal(float)
    done = Signal()
    failed = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.process = QProcess(self)
        self.process.readyReadStandardOutput.connect(self._output)
        self.process.readyReadStandardError.connect(self._error_output)
        self.process.finished.connect(self._finished)
        self.process.errorOccurred.connect(self._process_error)
        self.log = ''
        self.pending = ''
        self.duration = 1.0
        self.cancelled = False
        self.reported = False

    def start(self, args: list[str], duration: float):
        self.duration = max(0.001, duration)
        self.log, self.pending = '', ''
        self.cancelled = self.reported = False
        self.process.start(executable('ffmpeg'), args)

    def _output(self):
        self.pending += bytes(self.process.readAllStandardOutput()).decode('utf-8', 'replace')
        lines = self.pending.split('\n')
        self.pending = lines.pop()
        for line in lines:
            if line.startswith('out_time_us='):
                try:
                    self.progress.emit(min(0.99, max(0, int(line.split('=')[1]) / 1e6 / self.duration)))
                except ValueError:
                    pass

    def _error_output(self):
        self.log = (self.log + bytes(self.process.readAllStandardError()).decode('utf-8', 'replace'))[-8000:]

    def _process_error(self, error):
        if error == QProcess.ProcessError.FailedToStart:
            self._fail(self.process.errorString())

    def _fail(self, message):
        if not self.reported:
            self.reported = True
            self.failed.emit(message)

    def _finished(self, code, status):
        self._error_output()
        if self.cancelled:
            self._fail('Export cancelled.')
        elif code != 0 or status != QProcess.ExitStatus.NormalExit:
            self._fail(self.log or 'FFmpeg failed.')
        elif not self.reported:
            self.reported = True
            self.progress.emit(1)
            self.done.emit()

    def cancel(self):
        self.cancelled = True
        self.process.kill()
