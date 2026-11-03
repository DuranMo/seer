from app import screen_capture
from app.recorder import capture_args
from app.replay_capture import replay_args


def test_duplication_has_wall_clock_timestamps_and_no_gdi(tmp_path):
    for args in (capture_args(tmp_path / 'video.mp4', 30, (0, 0, 1920, 1080), 2),
                 replay_args(tmp_path, 30, (0, 0, 1920, 1080), 'libx264', 2)):
        assert 'gdigrab' not in args
        assert args[args.index('-use_wallclock_as_timestamps') + 1] == '1'
        assert 'ddagrab=output_idx=2:framerate=30:draw_mouse=1,hwdownload,format=bgra' in args


def test_unsupported_duplication_falls_back_without_guessing_monitor(monkeypatch):
    calls = []
    def run(args, **kwargs):
        calls.append(args)
        return 'ddagrab' if '-filters' in args else 'gdigrab'
    monkeypatch.setattr(screen_capture, 'executable', lambda _: 'ffmpeg')
    monkeypatch.setattr(screen_capture, 'run_checked', run)
    monkeypatch.setattr(screen_capture, 'primary_output', lambda _: None)
    assert screen_capture.choose_capture(30, (0, 0, 1920, 1080)) is None
    assert not any('-i' in args for args in calls)


def test_failed_duplication_probe_uses_compatibility_backend(monkeypatch):
    def run(args, **kwargs):
        if '-i' in args:
            raise RuntimeError('Access denied to duplication')
        return 'ddagrab gdigrab'
    monkeypatch.setattr(screen_capture, 'executable', lambda _: 'ffmpeg')
    monkeypatch.setattr(screen_capture, 'run_checked', run)
    monkeypatch.setattr(screen_capture, 'primary_output', lambda _: 0)
    assert screen_capture.choose_capture(30, (0, 0, 1920, 1080)) is None
