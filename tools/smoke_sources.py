"""Opt-in native WGC check: two test windows, tiled video, no desktop input."""
from pathlib import Path
import os
import sys
import tempfile
import argparse
import array
import math
import wave

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PySide6.QtCore import QTimer
from PySide6.QtGui import QColor, QPainter, QImage
from PySide6.QtWidgets import QApplication, QWidget
from app.capture_sources import Window, WindowRegion, process_created
from app.recorder import Recorder
from app.replay import Replay
from app.audio import AudioDevice
from app.video_utils import executable, probe, run_checked


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--replay', action='store_true')
    parser.add_argument('--app-audio', action='store_true')
    parser.add_argument('--fps', type=float, default=30, help='0 matches display refresh')
    parser.add_argument('--windows', type=int, default=2)
    options = parser.parse_args()
    app = QApplication([])
    app.setQuitOnLastWindowClosed(False)
    Path('.tools').mkdir(exist_ok=True)
    folder = Path(tempfile.mkdtemp(prefix='sources-', dir='.tools')).resolve()

    class TestWindow(QWidget):
        def __init__(self, color):
            super().__init__()
            self.color = QColor(color)
            self.setWindowTitle('Seer capture test ' + color)
            self.resize(320, 240)
        def paintEvent(self, _):
            painter = QPainter(self)
            painter.fillRect(self.rect(), self.color)

    if options.windows < 1:
        raise ValueError('Choose at least one test window.')
    sources = [TestWindow('green' if i % 2 == 0 else 'red') for i in range(options.windows)]
    for i, source in enumerate(sources):
        source.move(100 + i * 40, 100 + i * 40)
        source.show()  # Deliberately overlap the test windows.
    recorder = Replay() if options.replay else Recorder()
    errors = []
    selected = None
    results = []
    if options.app_audio:
        import winsound
        tone = folder / 'tone.wav'
        values = array.array('h', (int(8000 * math.sin(2 * math.pi * 440 * i / 48000)) for i in range(48000) for _ in range(2)))
        with wave.open(str(tone), 'wb') as out:
            out.setnchannels(2)
            out.setsampwidth(2)
            out.setframerate(48000)
            out.writeframes(values.tobytes())
        winsound.PlaySound(str(tone), winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_LOOP)

    def start():
        nonlocal selected
        created = process_created(os.getpid())
        selected = WindowRegion((0, 0, 640, 240), tuple(Window(int(w.winId()), os.getpid(), created,
            w.windowTitle()) for w in sources))
        from app.capture_rates import resolve_rate
        fps = resolve_rate(options.fps, selected)
        print(f'Capture rate: {fps} FPS; windows: {len(sources)}', flush=True)
        audio = AudioDevice('apps', 0, 'Test app', 2, 48000, (os.getpid(),)) if options.app_audio else None
        if options.replay:
            recorder.start(fps, audio, selected)
        else:
            recorder.start(folder, fps, audio, selected)

    def ready(path):
        try:
            if options.replay:
                results.append(path)
                path = path.state.media.path
            media = probe(path)
            assert (media.width, media.height) == (640, 240)
            preview = folder / 'preview.png'
            run_checked([executable('ffmpeg'), '-v', 'error', '-y', '-i', str(path), '-frames:v', '1', str(preview)])
            image = QImage(str(preview))
            columns = math.ceil(math.sqrt(len(sources)))
            rows = math.ceil(len(sources) / columns)
            for i in range(len(sources)):
                color = image.pixelColor((i % columns) * (640 // columns) + (640 // columns) // 2,
                                         (i // columns) * (240 // rows) + (240 // rows) // 2)
                if i % 2:
                    assert color.red() > color.green() * 2, (i, color.name())
                else:
                    assert color.green() > color.red() * 2, (i, color.name())
            if options.app_audio:
                audio_path = folder / 'check.wav'
                run_checked([executable('ffmpeg'), '-v', 'error', '-y', '-i', str(path), '-vn', '-c:a', 'pcm_s16le', str(audio_path)])
                with wave.open(str(audio_path), 'rb') as source:
                    samples = array.array('h', source.readframes(source.getnframes()))
                assert max(abs(v) for v in samples) > 1000, 'Selected app audio is missing.'
            print(f'OK: native selected-window capture, occluded content, {len(sources)}-window tiling; {media.duration:.2f}s; {path}', flush=True)
            if options.replay:
                if len(results) == 2:
                    recorder.stop()
            else:
                app.exit(0)
        except Exception as error:
            fail(str(error))

    def fail(message):
        errors.append(message)
        print('FAILED:', message, flush=True)
        recorder.stop()
        app.exit(1)

    def started():
        if recorder.window_video is not None:
            print(bytes(recorder.window_video.process.readAllStandardError()).decode('utf-8', 'replace').strip(), flush=True)
        if options.replay:
            recorder.begin_recording(folder / 'recording.mp4')
            QTimer.singleShot(1700, lambda: recorder.clip(1, folder / 'clip.mp4', permanent=True))
            QTimer.singleShot(2500, recorder.end_recording)
        else:
            QTimer.singleShot(1600, recorder.stop)

    recorder.started.connect(started)
    recorder.ready.connect(ready)
    recorder.failed.connect(fail)
    if options.replay:
        recorder.clip_failed.connect(fail)
        recorder.stopped.connect(lambda: app.exit(int(bool(errors))))
    QTimer.singleShot(300, start)
    QTimer.singleShot(25000, lambda: fail('Window capture timed out.'))
    return app.exec()


if __name__ == '__main__':
    raise SystemExit(main())
