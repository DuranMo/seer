"""Opt-in native desktop capture check; outputs remain in .tools for inspection."""
from __future__ import annotations

import argparse
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from app.audio import devices
from app.main_window import primary_geometry
from app.recorder import Recorder
from app.video_utils import probe


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fps', type=int, choices=[15, 24, 30, 60], default=30)
    parser.add_argument('--audio', choices=['none', 'system', 'microphone'], default='none')
    parser.add_argument('--device-index', type=int)
    parser.add_argument('--seconds', type=float, default=2)
    options = parser.parse_args()
    app = QApplication([])
    recorder = Recorder()
    folder = Path(tempfile.mkdtemp(prefix='smoke-', dir='.tools'))
    device = None
    if options.audio != 'none':
        found, message = devices()
        if options.device_index is None and options.audio == 'system':
            import pyaudiowpatch as pa
            with pa.PyAudio() as audio:
                options.device_index = audio.get_default_wasapi_loopback()['index']
        device = next((d for d in found if d.mode == options.audio and
                       (options.device_index is None or d.index == options.device_index)), None)
        if device is None:
            raise RuntimeError(message or 'No matching audio capture device. Run python -m pyaudiowpatch to list devices.')
        print('Device:', device.name, flush=True)

    def ready(path):
        try:
            media = probe(path)
            print(f'OK: {media.width}x{media.height}, {media.duration:.3f}s, '
                  f'{media.average_rate} FPS, audio={media.has_audio}; {path}', flush=True)
            app.exit(0)
        except Exception as error:
            failed(str(error))

    def failed(message):
        print('FAILED:', message, flush=True)
        app.exit(1)

    recorder.status.connect(lambda message: print(message, flush=True))
    recorder.started.connect(lambda: QTimer.singleShot(round(options.seconds * 1000), recorder.stop))
    recorder.ready.connect(ready)
    recorder.failed.connect(failed)
    QTimer.singleShot(0, lambda: recorder.start(folder, options.fps, device, primary_geometry()))
    return app.exec()


if __name__ == '__main__':
    raise SystemExit(main())
