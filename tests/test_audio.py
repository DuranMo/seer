from __future__ import annotations

import threading
import wave

import pytest

from app.audio import AudioCapture, AudioDevice


def test_audio_writer_keeps_silence_gaps(tmp_path, monkeypatch):
    pytest.importorskip('pyaudiowpatch')
    from app import audio
    monkeypatch.setattr(audio.time, 'time', lambda: 10000.0)
    device = AudioDevice('system', 1, 'test loopback', 1, 48000)
    path = tmp_path / 'loopback.wav'
    capture = AudioCapture(device, path)
    capture.thread = threading.Thread(target=capture._write)
    capture.thread.start()
    block = b'\1\0' * 480
    capture._callback(block, 480, {'input_buffer_adc_time': 100.0, 'current_time': 100.0}, 0)
    capture._callback(block, 480, {'input_buffer_adc_time': 100.5, 'current_time': 100.0}, 0)
    capture.stop()
    with wave.open(str(path), 'rb') as wav:
        assert wav.getnframes() == 24480
        samples = wav.readframes(wav.getnframes())
    assert samples[:960] == block
    assert samples[960:48000] == b'\0' * (48000 - 960)
    assert samples[48000:] == block


def test_audio_overflow_is_reported(tmp_path):
    pa = pytest.importorskip('pyaudiowpatch')
    capture = AudioCapture(AudioDevice('microphone', 1, 'test input', 1, 48000), tmp_path / 'audio.wav')
    _, status = capture._callback(b'\0' * 960, 480,
        {'input_buffer_adc_time': 100.0, 'current_time': 100.0}, pa.paInputOverflow)
    assert status == pa.paAbort
    assert 'overflowed' in capture.error


def test_long_loopback_silence_uses_bounded_writes(tmp_path, monkeypatch):
    from app import audio
    writes = []

    class Wave:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def setnchannels(self, value):
            pass

        def setsampwidth(self, value):
            pass

        def setframerate(self, value):
            pass

        def writeframesraw(self, data):
            writes.append(len(data))

    monkeypatch.setattr(audio.wave, 'open', lambda *args: Wave())
    capture = AudioCapture(AudioDevice('system', 1, 'test', 2, 48000), tmp_path / 'audio.wav')
    capture.first_time = 0
    capture.queue.put((b'\0' * 1920, 1800.0))
    capture.queue.put(None)
    capture._write()
    assert max(writes) <= 192000  # No allocation proportional to 30 minutes of silence.
    assert capture.samples == 1800 * 48000 + 480
