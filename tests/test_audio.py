from __future__ import annotations

import threading
import wave
import array
import math

import pytest

from app.audio import AudioCapture, AudioDevice


@pytest.mark.parametrize('rolling', [False, True])
def test_continuous_tone_is_not_spliced_by_callback_timestamp_jitter(tmp_path, monkeypatch, rolling):
    from app.rolling_audio import RollingAudio, assemble_audio
    from app.segments import SegmentRing
    rate = 48000
    monkeypatch.setattr('app.rolling_audio.time.time', lambda: 100.0)
    device = AudioDevice('system', 1, 'test', 1, rate)
    ring = SegmentRing(tmp_path)
    output = tmp_path / 'audio.wav'
    capture = RollingAudio(device, tmp_path, ring) if rolling else AudioCapture(device, output)
    capture.first_time = 100.0
    samples = array.array('h', (round(10000 * math.sin(2 * math.pi * 440 * i / rate)) for i in range(rate)))
    raw = samples.tobytes()
    for index in range(100):
        # Alternating early/late timestamps used to cut pieces out of every
        # block in replay, turning a clean tone into a distorted waveform.
        jitter = 0 if index == 0 else (0.001 if index % 2 else -0.001)
        capture.queue.put((raw[index * 960:(index + 1) * 960], 100 + index * 0.01 + jitter))
    capture.queue.put(None)
    capture._write()
    assert not capture.error
    if rolling:
        assemble_audio(ring.select(100, 101), 100, 101, device, output)
    with wave.open(str(output), 'rb') as source:
        assert source.getnframes() == rate
        assert source.readframes(rate) == raw


def test_callback_clock_mapping_does_not_inherit_scheduler_jitter(tmp_path, monkeypatch):
    pytest.importorskip('pyaudiowpatch')
    now = iter([1000.0, 1000.017])
    monkeypatch.setattr('app.audio.time.time', lambda: next(now))
    capture = AudioCapture(AudioDevice('system', 1, 'test', 1, 48000), tmp_path / 'audio.wav')
    for current in (10.0, 10.01):
        capture._callback(b'\0' * 960, 480,
                          {'input_buffer_adc_time': current - 0.02, 'current_time': current}, 0)
    assert capture.queue.get()[1] == pytest.approx(999.98)
    assert capture.queue.get()[1] == pytest.approx(999.99)


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
