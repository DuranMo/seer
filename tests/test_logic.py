from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from app.audio import mux_args
from app.exporter import ExportOptions, ExportTarget, export_args
from app.recorder import capture_args
from app.video_utils import Crop, Media, preview_crop, validate_trim


@pytest.fixture
def media(tmp_path):
    path = tmp_path / 'source with spaces.mp4'
    path.write_bytes(b'original')
    return Media(path, 1920, 1080, 10, True, '30000/1001', '60/1', '1/1000000', 0,
                 (0, 0.02, 0.09), True)


def test_crop_accounts_for_letterboxing():
    assert preview_crop((100, 100, 400, 225), (100, 100, 800, 450), 1920, 1080) == Crop(0, 0, 960, 540)
    assert preview_crop((300, 212.5, 400, 225), (100, 100, 800, 450), 1920, 1080) == Crop(480, 270, 960, 540)
    assert preview_crop((-100, -100, 2000, 2000), (0, 0, 800, 450), 1920, 1080) == Crop(0, 0, 1920, 1080)


@pytest.mark.parametrize('crop', [Crop(-1, 0, 10, 10), Crop(0, 0, 1, 10), Crop(1919, 0, 10, 10)])
def test_invalid_crop(crop):
    with pytest.raises(ValueError):
        crop.validate(1920, 1080)


@pytest.mark.parametrize('start,end', [(-1, 2), (3, 3), (4, 2), (0, 11), (float('nan'), 3)])
def test_invalid_trim(start, end):
    with pytest.raises(ValueError):
        validate_trim(start, end, 10)


def test_source_timing_and_safe_paths(media, tmp_path):
    options = ExportOptions('mp4', 1.123, 4.567, Crop(3, 5, 501, 301))
    args = export_args(media, options, tmp_path / 'output & name.mp4')
    assert str(media.path) in args
    assert args[args.index('-ss') + 1] == '1.123000'
    assert '-r' not in args
    assert 'fps=' not in args[args.index('-vf') + 1]
    assert args[args.index('-enc_time_base:v') + 1] == 'demux'
    assert 'crop=501:301:3:5:exact=1' in args[args.index('-vf') + 1]


@pytest.mark.parametrize('fmt', ['mp3', 'wav', 'flac', 'm4a'])
def test_audio_does_not_validate_video_settings(media, tmp_path, fmt):
    options = ExportOptions(fmt, 0.1, 3.2, Crop(-10, 0, 0, 0), 123, False)
    args = export_args(media, options, tmp_path / ('out.' + fmt))
    assert '-vn' in args and '-vf' not in args and '-fps_mode' not in args
    assert '-filter_complex' not in args
    with pytest.raises(ValueError, match='no audio'):
        export_args(replace(media, has_audio=False), options, tmp_path / ('out.' + fmt))


@pytest.mark.parametrize('rate', [15, 24, 30, 60])
@pytest.mark.parametrize('fmt', ['mp4', 'webm', 'gif', 'mov'])
def test_explicit_frame_rate(media, tmp_path, rate, fmt):
    args = export_args(media, ExportOptions(fmt, 0, 3, Crop(0, 0, 1920, 1080), rate), tmp_path / ('out.' + fmt))
    assert f'fps={rate}' in ' '.join(args)


def test_no_audio_and_gif(media, tmp_path):
    for fmt in ('mp4', 'gif'):
        args = export_args(replace(media, has_audio=False), ExportOptions(fmt, 0, 2, Crop(0, 0, 100, 100)), tmp_path / ('out.' + fmt))
        assert '-an' in args


def test_atomic_output_and_original_preservation(media, tmp_path):
    with pytest.raises(ValueError):
        ExportTarget(media.path, media.path, True)
    destination = tmp_path / 'existing.mp4'
    destination.write_bytes(b'keep me')
    with pytest.raises(FileExistsError):
        ExportTarget(destination, media.path)
    target = ExportTarget(destination, media.path, True)
    target.temporary.write_bytes(b'partial')
    target.cleanup()
    assert destination.read_bytes() == b'keep me'
    target = ExportTarget(destination, media.path, True)
    target.temporary.write_bytes(b'completed')
    target.commit()
    assert destination.read_bytes() == b'completed'
    assert media.path.read_bytes() == b'original'


def test_destination_created_during_export_is_preserved(media, tmp_path):
    destination = tmp_path / 'new.mp4'
    target = ExportTarget(destination, media.path)
    target.temporary.write_bytes(b'export')
    destination.write_bytes(b'other application')
    with pytest.raises(FileExistsError):
        target.commit()
    target.cleanup()
    assert destination.read_bytes() == b'other application'


def test_capture_settings_and_mux_offset(tmp_path):
    args = capture_args(tmp_path / 'capture.mp4', 60, (0, 0, 2560, 1440))
    assert args[args.index('-framerate') + 1] == '60'
    assert '2560x1440' in args
    args = mux_args(tmp_path / 'v.mp4', tmp_path / 'a.wav', tmp_path / 'o.mp4', -0.123456)
    assert args[args.index('-itsoffset') + 1] == '-0.123456'
