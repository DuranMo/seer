"""Opt-in real replay + simultaneous recording + clip + cleanup check."""
from __future__ import annotations

import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from app.audio import devices
from app.display import primary_geometry
from app.replay import Replay
from app.sessions import RecordingSessions


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--audio', choices=['none', 'system'], default='none')
    parser.add_argument('--software', action='store_true')
    options = parser.parse_args()
    app = QApplication([])
    app.setQuitOnLastWindowClosed(False)
    sessions = RecordingSessions()
    device = None
    if options.audio == 'system':
        import pyaudiowpatch as pa
        with pa.PyAudio() as backend:
            index = backend.get_default_wasapi_loopback()['index']
        found, error = devices()
        device = next((d for d in found if d.index == index), None)
        if device is None:
            raise RuntimeError(error or 'The default loopback device was unavailable.')
    replay = Replay()
    results = []
    errors = []
    original_pid = None
    folder = None

    def started():
        nonlocal original_pid, folder
        original_pid, folder = replay.process.processId(), replay.folder
        print('Replay ready:', replay.encoder, 'audio=', bool(device), flush=True)
        replay.video.retention = replay.sound.retention = 3
        replay.begin_recording(sessions.create() / 'screen.mp4')
        QTimer.singleShot(1500, clip)
        QTimer.singleShot(3500, replay.end_recording)

    def clip():
        assert replay.manual_active
        replay.clip(2, sessions.create() / 'screen.mp4')

    def ready(result):
        assert replay.running and replay.process.processId() == original_pid
        results.append(result)
        state = result.state
        print(f'{result.kind}: selected={state.end - state.start:.3f}s, audio={state.media.has_audio}', flush=True)
        assert state.media.has_audio == bool(device)
        if len(results) == 2:
            assert {r.kind for r in results} == {'clip', 'recording'}
            replay.clip(2, sessions.create() / 'screen.mp4')
        elif len(results) == 3:
            assert abs(result.state.end - result.state.start - 2) < 0.002
            QTimer.singleShot(600, stop)

    def stop():
        assert replay.running and not replay.manual_active
        assert replay.process.processId() == original_pid
        assert replay.video.bounds()[0] > replay.epoch, 'Old segments should expire once ranges are released.'
        replay.stop()

    def stopped():
        assert folder is None or not folder.exists(), 'Rolling buffer should be removed.'
        print('Replay stopped; buffer removed.', flush=True)
        app.quit()

    def fail(message):
        errors.append(message)
        print('FAILED:', message, flush=True)
        replay.stop(discard=True)

    replay.started.connect(started)
    replay.ready.connect(ready)
    replay.failed.connect(fail)
    replay.clip_failed.connect(fail)
    replay.stopped.connect(stopped)
    QTimer.singleShot(0, lambda: replay.start(30, device, primary_geometry(), software=options.software))
    QTimer.singleShot(35000, lambda: fail('Replay smoke test timed out.'))
    app.exec()
    remaining = sessions.cleanup()
    assert not remaining
    assert all(not r.state.media.path.exists() for r in results)
    if not errors and len(results) == 3:
        print('OK: one capture stream served replay and manual recording; clip/recording and cleanup passed.', flush=True)
        return 0
    return 1


if __name__ == '__main__':
    raise SystemExit(main())
