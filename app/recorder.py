from __future__ import annotations

from pathlib import Path
import re
import sys
import wave

from PySide6.QtCore import QObject, QProcess, QTimer, Signal

from .audio import AudioCapture, AudioDevice, mux_args
from .capture_rates import validate_rate
from .jobs import FFmpegJob, background
from .screen_capture import choose_capture, input_args
from .video_utils import executable, run_checked


def capture_args(output: Path, fps: int, geometry: tuple[int, int, int, int],
                 capture_output: int | None = None) -> list[str]:
    validate_rate(fps)
    return ['-hide_banner', '-y', '-copyts', '-filter_threads', '1'] + input_args(fps, geometry, capture_output) + ['-an', '-vf',
        'settb=1/1000000,showinfo,setpts=PTS-STARTPTS,pad=ceil(iw/2)*2:ceil(ih/2)*2',
        '-c:v', 'libx264', '-preset', 'ultrafast', '-crf', '18', '-pix_fmt', 'yuv420p',
        '-fps_mode', 'vfr', '-video_track_timescale', '1000000', str(output)]


class Recorder(QObject):
    started = Signal()
    ready = Signal(object)
    failed = Signal(str)
    status = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.process = QProcess(self)
        self.process.readyReadStandardError.connect(self._read)
        self.process.finished.connect(self._finished)
        self.process.errorOccurred.connect(self._error)
        self.watchdog = QTimer(self)
        self.watchdog.setSingleShot(True)
        self.watchdog.timeout.connect(self._timeout)
        self.audio = None
        self.window_video = None
        self.video_epoch = None
        self.busy = False
        self.stopping = False
        self.log = self.pending = ''
        self.fatal_error = ''
        self.folder = Path('.')
        self.mux = FFmpegJob(self)
        self.mux.done.connect(self._mux_done)
        self.mux.failed.connect(self._fail)

    def start(self, folder: Path, fps: int, device: AudioDevice | None,
              geometry: tuple[int, int, int, int]):
        if self.busy:
            return
        self.busy, self.stopping = True, False
        self.folder, self.video_epoch = folder, None
        self.geometry = geometry
        self.fps = fps
        self.log = self.pending = ''
        self.fatal_error = ''
        self.audio = None
        self.status.emit('Checking capture…')
        self.worker = background(self._prepare, (folder, fps, device, geometry), self._launch, self._fail)

    def _prepare(self, folder, fps, device, geometry):
        if sys.platform != 'win32':
            raise RuntimeError('Screen recording requires Windows 10 or 11. Video import works on other systems.')
        tool = executable('ffmpeg')
        executable('ffprobe')
        self.capture_output = choose_capture(fps, geometry)
        encoders = run_checked([tool, '-hide_banner', '-encoders'])
        if 'libx264' not in encoders:
            raise RuntimeError('This FFmpeg build needs the libx264 encoder.')
        args = capture_args(folder / 'screen.mp4', fps, geometry, self.capture_output)
        if device:
            self.audio = AudioCapture(device, folder / 'audio.wav')
            self.audio.start()
        return tool, args

    def _launch(self, command):
        if self.stopping:
            self.worker = background(self._stop_audio, result=self._cancelled_start, error=self._fail)
            return
        self.status.emit('Starting capture…')
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
                self.fatal_error = str(error)
                self.worker = background(self._stop_audio, result=self._launch_error_cleaned, error=self._fail)
                return
        self.process.start(*command)
        self.watchdog.start(15000)

    def _window_failed(self, message):
        self.fatal_error = message
        self.stop()

    def _cancelled_start(self, _):
        self._fail('Recording cancelled before capture began.')

    def _read(self):
        data = bytes(self.process.readAllStandardError()).decode('utf-8', 'replace')
        self.log = (self.log + data)[-8000:]
        self.pending += data
        lines = self.pending.split('\n')
        self.pending = lines.pop()[-8000:]
        for line in lines:
            if self.video_epoch is None:
                match = re.search(r'\bn:\s*0\s+.*?pts:\s*(-?\d+)\s', line)
                if match:
                    # gdigrab time_base is 1/1000000. pts_time is formatted with
                    # too few significant digits for epoch timestamps.
                    self.video_epoch = int(match.group(1)) / 1_000_000
                    if self.video_epoch < 1e9:
                        self.fatal_error = 'The screen capture clock is unavailable. Recording cannot be synchronized safely.'
                        self.process.kill()
                        return
                    self.watchdog.stop()
                    self.started.emit()

    def stop(self):
        if not self.busy or self.stopping:
            return
        self.stopping = True
        self.status.emit('Finishing recording…')
        if self.process.state() == QProcess.ProcessState.NotRunning:
            return  # Preparation will finish and release audio before reporting cancellation.
        if self.window_video is not None:
            self.window_video.stop()
        else:
            self.process.write(b'q\n')
        self.watchdog.start(15000)

    def _timeout(self):
        self.fatal_error = 'Screen capture timed out. Partial files are retained in the session folder.'
        if self.window_video is not None:
            self.window_video.kill()
        self.process.kill()

    def _error(self, error):
        if error == QProcess.ProcessError.FailedToStart:
            if self.window_video is not None:
                self.window_video.stop()
            self.fatal_error = self.process.errorString()
            self.worker = background(self._stop_audio, result=self._launch_error_cleaned, error=self._fail)

    def _launch_error_cleaned(self, _):
        self._fail(self.fatal_error)

    def _stop_audio(self):
        if self.audio:
            self.audio.stop()

    def _finished(self, code, status):
        if self.window_video is not None:
            self.window_video.stop()
        self.watchdog.stop()
        self._read()
        self.worker = background(self._finish_audio, (code, status == QProcess.ExitStatus.NormalExit),
                                 self._finalize, self._fail)

    def _finish_audio(self, code, normal):
        self._stop_audio()
        if self.fatal_error:
            raise RuntimeError(self.fatal_error)
        if code != 0 or not normal or self.video_epoch is None:
            raise RuntimeError(self.log or 'No video frames were recorded.')
        if self.audio and self.audio.error:
            raise RuntimeError(self.audio.error + '\nThe silent video is retained in ' + str(self.folder))
        if self.audio and self.audio.first_time is None and self.audio.device.mode == 'system' and self.audio.was_active:
            # WASAPI loopback need not send packets while the output is silent.
            duration = float(run_checked([executable('ffprobe'), '-v', 'error', '-show_entries',
                'format=duration', '-of', 'default=nw=1:nk=1', str(self.folder / 'screen.mp4')]))
            with wave.open(str(self.audio.path), 'wb') as out:
                out.setnchannels(self.audio.device.channels)
                out.setsampwidth(2)
                out.setframerate(self.audio.device.rate)
                remaining = round(duration * self.audio.device.rate)
                while remaining:
                    count = min(remaining, self.audio.device.rate)
                    out.writeframesraw(b'\0' * count * self.audio.device.channels * 2)
                    remaining -= count
            self.audio.first_time = self.video_epoch
        return self.audio is not None and self.audio.first_time is not None

    def _finalize(self, has_samples):
        if self.audio and not has_samples:
            self._fail('The selected device delivered no audio. The silent video is retained in ' + str(self.folder))
            return
        if has_samples:
            self.status.emit('Synchronizing audio…')
            args = mux_args(self.folder / 'screen.mp4', self.folder / 'audio.wav',
                self.folder / 'recording.mp4', self.audio.first_time - self.video_epoch)
            try:
                self.mux.start(args, 1)
            except Exception as error:
                self._fail(str(error))
        else:
            self.busy = False
            self.ready.emit(self.folder / 'screen.mp4')

    def _mux_done(self):
        self.busy = False
        self.ready.emit(self.folder / 'recording.mp4')

    def _fail(self, message):
        self.watchdog.stop()
        self.busy = False
        self.failed.emit(message)
