from __future__ import annotations

import array
import json
from pathlib import Path
import subprocess
from fractions import Fraction

import pytest

from app.audio import mux_args
from app.exporter import ExportOptions, export_args
from app.video_utils import Crop, executable, probe, run_checked


@pytest.fixture(scope='module')
def ffmpeg():
    try:
        tool = executable('ffmpeg')
        executable('ffprobe')
        return tool
    except RuntimeError as error:
        pytest.skip(str(error))


def metadata(path):
    return json.loads(run_checked([executable('ffprobe'), '-v', 'error', '-show_streams',
                                  '-show_format', '-of', 'json', str(path)]))


@pytest.fixture(scope='module')
def sample(tmp_path_factory, ffmpeg):
    path = tmp_path_factory.mktemp('media with spaces') / 'source vfr.mp4'
    run_checked([ffmpeg, '-hide_banner', '-y', '-f', 'lavfi', '-i', 'testsrc2=size=160x120:rate=60:duration=3',
        '-f', 'lavfi', '-i', 'sine=frequency=880:sample_rate=48000:duration=3',
        '-vf', "select='if(lt(t,1),not(mod(n,4)),if(lt(t,2),not(mod(n,2)),1))'",
        '-fps_mode', 'vfr', '-c:v', 'libx264', '-c:a', 'aac', str(path)])
    return probe(path)


@pytest.mark.parametrize('fmt', ['mp4', 'webm', 'gif', 'mov', 'mp3', 'wav', 'flac', 'm4a'])
def test_real_trim_crop_and_formats(sample, ffmpeg, tmp_path, fmt):
    output = tmp_path / ('result.' + fmt)
    options = ExportOptions(fmt, 0.4, 2.4, Crop(10, 12, 100, 80))
    run_checked([ffmpeg] + export_args(sample, options, output))
    data = metadata(output)
    duration = float(data['format']['duration'])
    assert abs(duration - 2) < 0.20, data
    video = [s for s in data['streams'] if s['codec_type'] == 'video']
    audio = [s for s in data['streams'] if s['codec_type'] == 'audio']
    if fmt in ('mp3', 'wav', 'flac', 'm4a'):
        assert audio and not video
    else:
        assert (video[0]['width'], video[0]['height']) == (100, 80)
        assert bool(audio) == (fmt != 'gif')


def test_vfr_preserves_actual_timestamps(sample, ffmpeg, tmp_path):
    assert sample.variable_rate
    quick = probe(sample.path, inspect_frames=False)
    assert quick.duration == sample.duration and quick.time_base == sample.time_base
    assert quick.timestamps == ()  # Editor can open before asynchronous frame inspection.
    output = tmp_path / 'preserved.mp4'
    run_checked([ffmpeg] + export_args(sample, ExportOptions('mp4', 0, 3, Crop(0, 0, 160, 120)), output))
    result = probe(output)
    assert len(result.timestamps) == len(sample.timestamps)
    for before, after in zip(sample.timestamps, result.timestamps):
        assert abs(before - after) < 0.00001


@pytest.mark.parametrize('rate', [15, 24, 30, 60])
def test_real_explicit_fps(sample, ffmpeg, tmp_path, rate):
    output = tmp_path / f'{rate}.mp4'
    run_checked([ffmpeg] + export_args(sample, ExportOptions('mp4', 0.2, 2.2, Crop(0, 0, 160, 120), rate), output))
    result = probe(output)
    assert abs(float(Fraction(result.average_rate)) - rate) < 0.01
    assert abs(result.duration - 2) < 0.05


@pytest.mark.parametrize('offset', [-0.25, 0.35])
def test_audio_mux_timing(ffmpeg, sample, tmp_path, offset):
    audio, output = tmp_path / 'audio.wav', tmp_path / 'synced.mp4'
    run_checked([ffmpeg, '-v', 'error', '-y', '-f', 'lavfi', '-i',
        r"aevalsrc=if(gte(t\,0.5)\,0.5*sin(2*PI*440*t)\,0):s=48000:d=3", str(audio)])
    run_checked([ffmpeg] + mux_args(sample.path, audio, output, offset))
    result = metadata(output)
    streams = result['streams']
    assert abs(float(streams[0]['duration']) - float(streams[1]['duration'])) < 0.1
    raw = subprocess.run([ffmpeg, '-v', 'error', '-i', str(output), '-vn', '-f', 's16le',
        '-ac', '1', '-ar', '48000', 'pipe:1'], capture_output=True, check=True).stdout
    samples = array.array('h', raw)
    first = next(i for i, value in enumerate(samples) if abs(value) > 1500) / 48000
    assert abs(first - (0.5 + offset)) < 0.035


def test_timestamp_trim_preserves_audio_event(sample, ffmpeg, tmp_path):
    source = tmp_path / 'event.mp4'
    run_checked([ffmpeg, '-hide_banner', '-y', '-i', str(sample.path), '-f', 'lavfi', '-i',
        r"aevalsrc=if(gte(t\,1.0)\,0.5*sin(2*PI*440*t)\,0):s=48000:d=3",
        '-map', '0:v:0', '-map', '1:a:0', '-c:v', 'copy', '-c:a', 'aac', str(source)])
    media = probe(source)
    output = tmp_path / 'trimmed.wav'
    run_checked([ffmpeg] + export_args(media, ExportOptions('wav', 0.4, 2.4, Crop(0, 0, 160, 120)), output))
    import wave
    with wave.open(str(output), 'rb') as wav:
        samples = array.array('h', wav.readframes(wav.getnframes()))
        first = next(i for i, value in enumerate(samples) if abs(value) > 1500) / wav.getframerate()
        assert abs(first - 0.6) < 0.035
