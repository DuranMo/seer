"""One rolling capture stream, with independent replay and recording ranges."""
from __future__ import annotations

from collections import deque
import csv
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
import re
import shutil
import sys
import tempfile
import time

from PySide6.QtCore import QObject, QProcess, QTimer, Signal

from .audio import AudioDevice
from .jobs import background
from .replay_capture import choose_encoder, replay_args
from .replay_export import build_replay
from .rolling_audio import RollingAudio
from .screen_capture import choose_capture
from .segments import Segment, SegmentRing
from .video_utils import executable


@dataclass
class Request:
    start: float
    end: float
    destination: Path
    kind: str
    leases: tuple[int, int]
    deadline: float
    permanent: bool = False


class Replay(QObject):
    started = Signal()
    stopped = Signal()
    changed = Signal()
    ready = Signal(object)
    failed = Signal(str)
    clip_failed = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.process = QProcess(self)
        self.process.readyReadStandardError.connect(self._read)
        self.process.finished.connect(self._capture_finished)
        self.process.errorOccurred.connect(self._process_error)
        self.timer = QTimer(self)
        self.timer.setInterval(200)
        self.timer.timeout.connect(self._tick)
        self.watchdog = QTimer(self)
        self.watchdog.setSingleShot(True)
        self.watchdog.timeout.connect(self._timeout)
        self.active = self.running = self.stopping = self.preparing = False
        self.capture_done = self.audio_done = True
        self.audio = None
        self.folder = None
        self.video = self.sound = None
        self.epoch = self.latest_epoch = None
        self.last_frame = 0.0
        self.time_base = 0.000001
        self.encoder = ''
        self.capture_output = None
        self.window_video = None
        self.manual_start = None
        self.manual_destination = None
        self.manual_leases = None
        self.requests: deque[Request] = deque()
        self.current = None
        self.log = self.pending = self.error = ''
        self._audio_stopping = False
        self._cleaning = False

    @property
    def manual_active(self):
        return self.manual_start is not None

    @property
    def clip_pending(self):
        return any(r.kind == 'clip' for r in list(self.requests) + ([self.current] if self.current else []))

    @property
    def manual_finishing(self):
        return any(r.kind == 'recording' for r in list(self.requests) + ([self.current] if self.current else []))

    @property
    def available(self):
        bounds = self.video.bounds() if self.video else None
        first = bounds[0] if bounds else self.epoch
        return min(120.0, max(0, (self.latest_epoch or 0) - (first or self.latest_epoch or 0)))

    def start(self, fps: int, device: AudioDevice | None, geometry, *, software=False):
        if self.active:
            return
        self.active = self.preparing = True
        self.running = self.stopping = self._cleaning = self._audio_stopping = False
        self.capture_done = self.audio_done = True
        self.epoch = self.latest_epoch = None
        self.audio = None
        self.log = self.pending = self.error = ''
        self.manual_start = self.manual_leases = None
        self.folder = Path(tempfile.mkdtemp(prefix='seer-replay-')).resolve()
        self.video, self.sound = SegmentRing(self.folder), SegmentRing(self.folder)
        self.fps, self.geometry = fps, geometry
        self.timer.start()
        self.changed.emit()
        self.worker = background(self._prepare, (fps, device, geometry, software), self._prepared, self._prepare_failed)

    def _prepare(self, fps, device, geometry, software):
        if sys.platform != 'win32':
            raise RuntimeError('Replay capture requires Windows 10 or 11.')
        executable('ffmpeg')
        executable('ffprobe')
        self.capture_output = choose_capture(fps, geometry)
        encoder = 'libx264' if software else choose_encoder(geometry[2], geometry[3], fps)
        if device:
            self.audio = RollingAudio(device, self.folder, self.sound)
            self.audio_done = False
            self.audio.start()
        return encoder

    def _prepared(self, encoder):
        self.preparing = False
        self.encoder = encoder
        if self.stopping:
            self._stop_audio()
        else:
            self.capture_done = False
            if not self._launch_capture():
                return
            self.watchdog.start(15000)

    def _launch_capture(self):
        try:
            tool = executable('ffmpeg')
            args = replay_args(self.folder, self.fps, self.geometry, self.encoder, self.capture_output)
        except (RuntimeError, OSError, ValueError) as error:
            self.capture_done = True
            self.error = str(error)
            self.stop(discard=True)
            return False
        if self.window_video is not None:
            self.window_video.kill()
            self.window_video.deleteLater()
            self.window_video = None
        if self.capture_output == 'windows':
            from .window_video import WindowVideo
            self.window_video = WindowVideo(self.process, self)
            self.window_video.failed.connect(self._window_failed)
            try:
                self.window_video.start(self.fps, self.geometry)
            except (RuntimeError, OSError) as error:
                self.capture_done = True
                self.error = str(error)
                self.stop(discard=True)
                return False
        self.process.start(tool, args)
        return True

    def _window_failed(self, message):
        self.error = message
        self.stop(discard=True)

    def _prepare_failed(self, message):
        self.preparing = False
        self.error = message
        self.stop(discard=True)

    def _reserve(self, start, end=float('inf')):
        return self.video.reserve(start, end), self.sound.reserve(start, end)

    def _release(self, leases):
        self.video.release(leases[0])
        self.sound.release(leases[1])

    def begin_recording(self, destination: Path | None = None):
        if not self.running or self.stopping:
            raise RuntimeError('Replay is still starting. Wait for capture to become ready.')
        if self.manual_active or self.manual_finishing:
            raise RuntimeError('A recording is already running or finishing.')
        self.manual_start = max(self.epoch, time.time())
        self.manual_destination = destination
        self.manual_leases = self._reserve(self.manual_start)
        self.changed.emit()

    def end_recording(self, destination: Path | None = None, *, end: float | None = None):
        if not self.manual_active:
            return
        start, end = self.manual_start, time.time() if end is None else end
        destination = destination or self.manual_destination
        if destination is None:
            raise ValueError('A destination is required for the recording.')
        leases = self._reserve(start, end)
        self._release(self.manual_leases)
        self.manual_start = self.manual_leases = None
        self.requests.append(Request(start, end, destination, 'recording', leases, time.monotonic() + 15))
        self.changed.emit()

    def clip(self, seconds: int, destination: Path, *, permanent=False):
        if not 1 <= seconds <= 120:
            raise ValueError('Clip length must be between 1 and 120 seconds.')
        if not self.running or self.stopping:
            raise RuntimeError('Enable replay and wait for capture to be ready first.')
        if self.clip_pending:
            raise RuntimeError('The previous clip is still finishing. Try again in a moment.')
        end = time.time()
        start = max(self.epoch, end - seconds)
        self.requests.append(Request(start, end, destination, 'clip', self._reserve(start, end), time.monotonic() + 15, permanent))
        self.changed.emit()

    def stop(self, discard=False):
        if not self.active:
            return
        self.stopping = True
        self.running = False
        if self.manual_leases is not None and not (self.error and self.manual_destination):
            self._release(self.manual_leases)
            self.manual_start = self.manual_leases = None
        if discard:
            while self.requests:
                self._release(self.requests.popleft().leases)
        if self.preparing:
            return
        if self.window_video is not None:
            self.window_video.stop()
        if self.process.state() != QProcess.ProcessState.NotRunning:
            if self.window_video is None:
                self.process.write(b'q\n')
            self.watchdog.start(5000)
        else:
            self._stop_audio()
        self.changed.emit()

    def _timeout(self):
        self.error = self.error or 'Replay capture timed out. Restart replay to try again.'
        if self.process.state() != QProcess.ProcessState.NotRunning:
            if self.window_video is not None:
                self.window_video.kill()
            self.process.kill()
        else:
            self.stop(discard=True)

    def _process_error(self, error):
        if error == QProcess.ProcessError.FailedToStart:
            self.capture_done = True
            self.error = self.process.errorString()
            self.stop(discard=True)

    def _read(self):
        data = bytes(self.process.readAllStandardError()).decode('utf-8', 'replace')
        self.log = (self.log + data)[-8000:]
        self.pending += data
        lines = self.pending.split('\n')
        self.pending = lines.pop()[-8000:]
        for line in lines:
            base = re.search(r'config in time_base:\s*(\d+)/(\d+)', line)
            if base:
                self.time_base = float(Fraction(int(base[1]), int(base[2])))
            match = re.search(r'\bn:\s*\d+\s+.*?pts:\s*(-?\d+)\s', line)
            if not match:
                continue
            stamp = int(match[1]) * self.time_base
            if stamp < 1e9:
                self.error = 'The replay capture clock is unavailable.'
                self.stop(discard=True)
                return
            if self.latest_epoch is not None and (stamp < self.latest_epoch - 0.1 or stamp - self.latest_epoch > 5):
                self.error = 'Replay capture was interrupted or the system clock changed. Restart replay.'
                self.stop(discard=True)
                return
            self.latest_epoch, self.last_frame = stamp, time.monotonic()
            if self.epoch is None:
                self.epoch = stamp
                self.watchdog.stop()
                if not self.stopping:
                    self.running = True
                    self.started.emit()
                    self.changed.emit()

    def _capture_finished(self, code, status):
        if self.window_video is not None:
            self.window_video.stop()
        self._read()
        self.capture_done = True
        self.running = False
        self.watchdog.stop()
        if code or status != QProcess.ExitStatus.NormalExit:
            if self.epoch is None and self.encoder != 'libx264' and not self.stopping:
                # A listed/probed encoder can still become unavailable when a game
                # consumes its resources. Retry once before any history exists.
                self.encoder = 'libx264'
                self.capture_done = False
                self.log = self.pending = ''
                if not self._launch_capture():
                    return
                self.watchdog.start(15000)
                return
            self.error = self.error or self.log[-4000:] or 'Replay capture stopped unexpectedly.'
        elif not self.stopping:
            self.error = 'Replay capture stopped unexpectedly.'
        self.stopping = True
        self._read_segments()
        if self.manual_active:
            bounds = self.video.bounds()
            if self.manual_destination and bounds and bounds[1] > self.manual_start:
                self.end_recording(end=min(time.time(), bounds[1]))
            elif self.manual_leases is not None:
                self._release(self.manual_leases)
                self.manual_start = self.manual_leases = None
                self.clip_failed.emit('The recording stopped before any complete video was available.')
        self._stop_audio()
        self.changed.emit()

    def _stop_audio(self):
        if self._audio_stopping:
            return
        self._audio_stopping = True
        if self.audio:
            self.audio_worker = background(self.audio.stop, result=self._audio_stopped, error=self._audio_failed)
        else:
            self._audio_stopped(None)

    def _audio_stopped(self, _):
        self.audio_done = True
        if self.audio and self.audio.error:
            self.error = self.error or self.audio.error

    def _audio_failed(self, message):
        self.error = self.error or message
        self.audio_done = True

    def _read_segments(self):
        if self.epoch is None:
            return
        try:
            with (self.folder / 'segments.csv').open(encoding='utf-8') as source:
                for row in csv.reader(source):
                    if len(row) != 3 or not re.fullmatch(r'v-\d{10}\.ts', Path(row[0]).name):
                        continue
                    path = self.folder / Path(row[0]).name
                    if path.is_file():
                        self.video.add(Segment(path, self.epoch + float(row[1]), self.epoch + float(row[2])))
        except (OSError, ValueError):
            pass  # FFmpeg may be atomically replacing the short live list.

    def _tick(self):
        self._read_segments()
        if self.running:
            if self.audio and self.audio.error:
                self.error = self.audio.error
            elif time.monotonic() - self.last_frame > 5:
                self.error = 'Replay stopped receiving frames. Restart replay.'
            elif shutil.disk_usage(self.folder).free < 256 * 1024 * 1024:
                self.error = 'Replay stopped because the temporary drive is nearly full.'
            if self.error:
                self.stop(discard=True)
        self.video.prune(time.time())
        self.sound.prune(time.time())
        self._dispatch()
        if self.stopping and not self.preparing and self.capture_done and self.audio_done and not self.current and not self.requests and not self._cleaning:
            self._cleaning = True
            self.cleanup_worker = background(self._remove_buffer, result=self._cleaned, error=self._cleanup_failed)

    def _dispatch(self):
        if self.current is not None or not self.requests:
            return
        request = self.requests[0]
        video = self.video.select(request.start, request.end)
        complete = bool(video and video[-1].end >= request.end)
        if complete and self.audio:
            bounds = self.sound.bounds()
            complete = bool(bounds and bounds[1] >= video[-1].end)
        if not complete:
            if time.monotonic() > request.deadline or (self.capture_done and self.audio_done):
                self.requests.popleft()
                self._release(request.leases)
                self.clip_failed.emit('Capture ended before the requested clip could be finalized.')
                self.changed.emit()
            return
        self.current = self.requests.popleft()
        audio = self.sound.select(video[0].start, video[-1].end) if self.audio else []
        device = self.audio.device if self.audio else None
        self.job_worker = background(build_replay,
            (video, audio, device, request.start, request.end, request.destination, request.kind, request.permanent),
            self._built, self._build_failed)

    def _built(self, result):
        self._release(self.current.leases)
        self.current = None
        self.ready.emit(result)
        self.changed.emit()

    def _build_failed(self, message):
        retained = self.current.kind == 'recording'
        if not retained:
            self._release(self.current.leases)
        self.current = None
        note = f' Recording segments remain in {self.folder} until replay is disabled or Seer quits.' if retained else ''
        self.clip_failed.emit('Could not finalize the clip: ' + message + note)
        self.changed.emit()

    def _remove_buffer(self):
        for path in self.folder.iterdir():
            if path.resolve().parent == self.folder and (re.fullmatch(r'[va]-\d{10}\.(ts|wav)', path.name)
                    or path.name in ('segments.csv', 'segments.csv.tmp')):
                path.unlink(missing_ok=True)
        self.folder.rmdir()

    def _cleaned(self, _):
        self.timer.stop()
        self.active = self.stopping = self.running = False
        self.audio = None
        self.video = self.sound = None
        if self.error:
            self.failed.emit(self.error)
        self.stopped.emit()
        self.changed.emit()

    def _cleanup_failed(self, message):
        self.error = self.error or 'Replay buffer cleanup failed: ' + message
        self._cleaned(None)
