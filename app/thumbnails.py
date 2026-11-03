"""One bounded FFmpeg thumbnail job at a time; no persistent video decoder."""
from __future__ import annotations

import hashlib
from pathlib import Path
from uuid import uuid4

from PySide6.QtCore import QObject, QProcess, QStandardPaths, QTimer, Signal

from .video_utils import executable


def cache_path(source: Path, cache: Path) -> Path:
    stat = source.stat()
    key = f'v2:{source.resolve()}:{stat.st_size}:{stat.st_mtime_ns}'
    return cache / (hashlib.sha256(key.encode('utf-8')).hexdigest() + '.png')


def thumbnail_args(source: Path, output: Path) -> list[str]:
    return ['-hide_banner', '-v', 'error', '-nostdin', '-y', '-threads', '1',
            '-filter_threads', '1', '-i', str(source), '-map', '0:v:0',
            '-an', '-sn', '-dn', '-frames:v', '1', '-vf',
            'scale=320:180:force_original_aspect_ratio=decrease,pad=320:180:(ow-iw)/2:(oh-ih)/2',
            '-threads', '1', '-update', '1', str(output)]


class Thumbnails(QObject):
    ready = Signal(object, object)
    idle = Signal()

    def __init__(self, parent=None, cache=None):
        super().__init__(parent)
        self.cache = Path(cache) if cache else Path(QStandardPaths.writableLocation(
            QStandardPaths.StandardLocation.CacheLocation)) / 'thumbnails'
        self.process = QProcess(self)
        self.process.setStandardOutputFile(QProcess.nullDevice())
        self.process.setStandardErrorFile(QProcess.nullDevice())
        self.process.finished.connect(self._finished)
        self.process.errorOccurred.connect(self._error)
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.process.kill)
        self.current = None
        self.failed = set()

    def request(self, source: Path) -> Path | None:
        try:
            target = cache_path(source, self.cache)
            if target.is_file():
                return target
            if self.current is not None or target in self.failed:
                return None
            tool = executable('ffmpeg')
            self.cache.mkdir(parents=True, exist_ok=True)
            stage = target.with_name(target.stem + '.' + uuid4().hex + '.tmp.png')
            self.current = source, target, stage
            self.timer.start(8000)
            self.process.start(tool, thumbnail_args(source, stage))
        except (OSError, RuntimeError):
            # Keep filenames/descriptions usable when a clip or FFmpeg is unavailable.
            return None
        return None

    def _error(self, error):
        if error == QProcess.ProcessError.FailedToStart:
            self._finished(-1, QProcess.ExitStatus.CrashExit)

    def _finished(self, code, status):
        self.timer.stop()
        if self.current is None:
            return
        source, target, stage = self.current
        self.current = None
        try:
            if code == 0 and status == QProcess.ExitStatus.NormalExit and stage.is_file():
                # Don't publish a frame under an old fingerprint if the clip changed.
                if cache_path(source, self.cache) == target:
                    stage.replace(target)
                    self.ready.emit(source, target)
                else:
                    self.failed.add(target)
            else:
                self.failed.add(target)
        except OSError:
            self.failed.add(target)
        finally:
            try:
                stage.unlink(missing_ok=True)
            except OSError:
                pass
        self.idle.emit()

    def stop(self):
        self.timer.stop()
        if self.process.state() != QProcess.ProcessState.NotRunning:
            self.process.kill()
            self.process.waitForFinished(1000)
