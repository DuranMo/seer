import pytest
from app import capture_rates as rates
from app.capture_sources import Window, WindowRegion
from app.recorder import capture_args
from app.replay_capture import replay_args


class Screen:
    def __init__(self, name, fps):
        self.identifier, self.fps = name, fps
    def name(self):
        return self.identifier
    def refreshRate(self):
        return self.fps


def test_same_as_source_uses_primary_refresh_and_fastest_selected_monitor(monkeypatch):
    primary, other = Screen('primary', 59.94), Screen('other', 144.0)
    class Screens:
        @staticmethod
        def primaryScreen(): return primary
        @staticmethod
        def screens(): return [primary, other]
    monkeypatch.setattr(rates, 'QGuiApplication', Screens)
    monkeypatch.setattr(rates.sys, 'platform', 'win32')
    monkeypatch.setattr(rates, 'monitor_name', lambda handle: 'primary' if handle == 1 else 'other')
    monkeypatch.setattr(rates, 'monitor_rate', lambda name: 59.94 if name == 'primary' else 144.0)
    assert rates.resolve_rate(0, (0, 0, 640, 480)) == 59.94
    selected = WindowRegion((0, 0, 640, 480), (Window(1, 10, 100, 'a'), Window(2, 20, 200, 'b')))
    assert rates.resolve_rate(0, selected) == 144
    assert rates.resolve_rate(30, selected) == 30


@pytest.mark.parametrize('fps', [75, 120, 144, 240, 59.94])
def test_capture_commands_accept_high_and_fractional_source_rates(tmp_path, fps):
    args = capture_args(tmp_path / 'video.mp4', fps, (0, 0, 640, 480))
    assert args[args.index('-framerate') + 1] == str(fps)
    args = replay_args(tmp_path, fps, (0, 0, 640, 480), 'libx264')
    assert int(args[args.index('-g') + 1]) == round(fps)
