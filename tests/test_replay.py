from __future__ import annotations

import array
import csv
from pathlib import Path
import wave

import pytest

from app.audio import AudioDevice
from app.exporter import ExportOptions, export_args
from app.replay_capture import replay_args
from app.replay_export import build_replay
from app.rolling_audio import assemble_audio
from app.rolling_audio import RollingAudio
from app.segments import Segment, SegmentRing
from app.video_utils import Crop, executable, probe, run_checked


def test_rolling_segments_expire_but_recording_and_clip_leases_preserve_them(tmp_path):
    ring = SegmentRing(tmp_path, retention=3)
    for index in range(12):
        path = tmp_path / f'v-{index:010d}.ts'
        path.write_bytes(b'segment')
        ring.add(Segment(path, index, index + 1))
    recording = ring.reserve(3)
    clip = ring.reserve(0.5, 2.5)
    ring.prune(12)
    assert len(ring.select(0, 12)) == 12
    ring.release(clip)
    ring.prune(12)
    assert not (tmp_path / 'v-0000000000.ts').exists()
    assert (tmp_path / 'v-0000000003.ts').exists()
    ring.release(recording)
    ring.prune(12)
    assert all(s.end >= 9 for s in ring.select(0, 12))
    assert all(s.path.exists() for s in ring.select(9, 12))


def test_ring_rejects_paths_outside_its_buffer(tmp_path):
    ring = SegmentRing(tmp_path / 'buffer')
    with pytest.raises(ValueError):
        ring.add(Segment(tmp_path / 'user.mp4', 0, 1))


def write_wave(path, samples, rate=48000):
    with wave.open(str(path), 'wb') as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(rate)
        out.writeframes(array.array('h', samples).tobytes())


def test_audio_assembly_uses_timestamps_and_keeps_gaps(tmp_path):
    first, second = tmp_path / 'first.wav', tmp_path / 'second.wav'
    write_wave(first, [1000] * 48000)
    write_wave(second, [2000] * 48000)
    segments = [Segment(first, 10, 11), Segment(second, 12, 13)]
    output = tmp_path / 'joined.wav'
    assemble_audio(segments, 10.5, 12.5, AudioDevice('system', 1, 'test', 1, 48000), output)
    with wave.open(str(output), 'rb') as source:
        data = array.array('h', source.readframes(source.getnframes()))
    assert len(data) == 96000
    assert set(data[:24000]) == {1000}
    assert set(data[24000:72000]) == {0}
    assert set(data[72000:]) == {2000}


def test_rolling_audio_positions_samples_by_capture_clock(tmp_path, monkeypatch):
    monkeypatch.setattr('app.rolling_audio.time.time', lambda: 100.0)
    ring = SegmentRing(tmp_path)
    device = AudioDevice('system', 1, 'test', 1, 48000)
    capture = RollingAudio(device, tmp_path, ring)
    capture.queue.put((array.array('h', [1000] * 12000).tobytes(), 100.25))
    capture.queue.put((array.array('h', [2000] * 12000).tobytes(), 101.25))
    capture.queue.put(None)
    capture._write()
    assert not capture.error
    assert ring.bounds() == (100, 101.5)
    output = tmp_path / 'combined.wav'
    assemble_audio(ring.select(100, 102), 100, 101.5, device, output)
    with wave.open(str(output), 'rb') as source:
        data = array.array('h', source.readframes(source.getnframes()))
    assert set(data[:12000]) == {0}
    assert set(data[12000:24000]) == {1000}
    assert set(data[24000:60000]) == {0}
    assert set(data[60000:]) == {2000}


@pytest.fixture(scope='module', params=[15, 24, 30, 60])
def capture_segments(tmp_path_factory, request):
    try:
        ffmpeg = executable('ffmpeg')
        executable('ffprobe')
    except RuntimeError as error:
        pytest.skip(str(error))
    folder = tmp_path_factory.mktemp("replay space's")
    fps = request.param
    args = replay_args(folder, fps, (0, 0, 160, 120), 'libx264')
    index = args.index('-i')
    args = ['-hide_banner', '-y', '-f', 'lavfi', '-i', f'testsrc2=size=160x120:rate={fps}:duration=6.5'] + args[index + 2:]
    run_checked([ffmpeg] + args)
    segments = []
    with (folder / 'segments.csv').open() as source:
        for name, start, end in csv.reader(source):
            segments.append(Segment(folder / Path(name).name, 100 + float(start), 100 + float(end)))
    return folder, segments


def test_replay_source_and_exact_export_selection(capture_segments, tmp_path):
    folder, segments = capture_segments
    chosen = [s for s in segments if s.end > 101.2 and s.start < 104.7]
    source = tmp_path / 'clip.mp4'
    result = build_replay(chosen, [], None, 101.2, 104.7, source, 'clip')
    assert result.state.start == pytest.approx(0.2)
    assert result.state.end - result.state.start == pytest.approx(3.5, abs=0.04)
    assert result.state.media.has_audio is False
    exported = tmp_path / 'exported.mp4'
    state = result.state
    run_checked([executable('ffmpeg')] + export_args(state.media,
        ExportOptions('mp4', state.start, state.end, Crop(0, 0, 160, 120)), exported))
    assert probe(exported).duration == pytest.approx(3.5, abs=0.04)


def test_missing_video_segments_are_reported_instead_of_shifting_audio(capture_segments, tmp_path):
    _, segments = capture_segments
    with pytest.raises(RuntimeError, match='capture gap'):
        build_replay([segments[0], segments[2]], [], None, 100, 103, tmp_path / 'gap.mp4', 'clip')


def test_full_two_minute_clip_selection(capture_segments, tmp_path):
    _, source = capture_segments
    # Reuse a generated one-second GOP to exercise a real 120-second assembly
    # without spending two minutes recording a test desktop.
    segments = [Segment(source[0].path, 100 + i, 101 + i) for i in range(124)]
    chosen = [s for s in segments if s.end > 101.2 and s.start < 221.2]
    result = build_replay(chosen, [], None, 101.2, 221.2, tmp_path / 'two-minutes.mp4', 'clip')
    assert result.state.end - result.state.start == pytest.approx(120, abs=0.001)


def test_permanent_clip_is_trimmed_and_ready_without_export(capture_segments, tmp_path):
    _, segments = capture_segments
    chosen = [s for s in segments if s.end > 101.2 and s.start < 104.7]
    output = tmp_path / 'Videos' / 'Seer' / 'clip.mp4'
    output.parent.mkdir(parents=True)
    result = build_replay(chosen, [], None, 101.2, 104.7, output, 'clip', True)
    assert output.is_file()
    assert result.state.media.path == output.resolve()
    assert not result.state.temporary_source
    assert result.state.start == 0
    assert result.state.end == pytest.approx(3.5, abs=0.04)


def test_replay_audio_event_stays_synchronized_after_trim(capture_segments, tmp_path):
    _, segments = capture_segments
    audio = tmp_path / 'event.wav'
    run_checked([executable('ffmpeg'), '-y', '-v', 'error', '-f', 'lavfi', '-i',
        r'aevalsrc=if(gte(t\,2)\,0.5*sin(2*PI*440*t)\,0):s=48000:d=6.5', str(audio)])
    chosen = [s for s in segments if s.end > 101.2 and s.start < 104.7]
    result = build_replay(chosen, [Segment(audio, 100, 106.5)], AudioDevice('system', 1, 'test', 1, 48000),
                          101.2, 104.7, tmp_path / 'synced.mp4', 'clip')
    state = result.state
    output = tmp_path / 'trimmed.wav'
    run_checked([executable('ffmpeg')] + export_args(state.media,
        ExportOptions('wav', state.start, state.end, Crop(0, 0, 160, 120)), output))
    with wave.open(str(output), 'rb') as source:
        data = array.array('h', source.readframes(source.getnframes()))
        first = next(i for i, value in enumerate(data) if abs(value) > 1500) / source.getframerate()
    assert first == pytest.approx(0.8, abs=0.035)
