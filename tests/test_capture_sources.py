import pytest
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtCore import QSettings

from app import capture_sources as sources
from app.capture_sources import Window, WindowRegion, load_selection, save_selection, root_process_ids
from app import screen_capture


def test_saved_window_identity_round_trip_and_corrupt_selection_fails_closed(tmp_path):
    settings = QSettings(str(tmp_path / 'sources.ini'), QSettings.Format.IniFormat)
    selected = (Window(120, 42, 987654321, 'Game'),)
    save_selection(settings, 'sources/video', selected)
    assert load_selection(settings, 'sources/video') == selected
    settings.setValue('sources/video', '{invalid')
    with pytest.raises(RuntimeError, match='invalid'):
        load_selection(settings, 'sources/video')
    assert load_selection(settings, 'sources/video', strict=False) == ()


def test_stale_window_or_reused_process_is_rejected(monkeypatch):
    window = Window(120, 42, 1234, 'Game')
    monkeypatch.setattr(sources, 'window_pid', lambda _: 42)
    monkeypatch.setattr(sources, 'process_created', lambda _: 9999)
    with pytest.raises(RuntimeError, match='no longer available'):
        sources.validate((window,))
    monkeypatch.setattr(sources, 'process_created', lambda _: 1234)
    monkeypatch.setattr(sources, 'window_pid', lambda _: 99)
    with pytest.raises(RuntimeError):
        sources.validate((window,))
    sources.validate((window,), audio=True)  # Audio can continue after a window closes.


def test_all_video_keeps_existing_capture_path_without_native_helper(monkeypatch):
    geometry = (0, 0, 1920, 1080)
    assert sources.region(geometry, ()) is geometry
    tools = []
    monkeypatch.setattr(screen_capture, 'executable', lambda name: tools.append(name) or name)
    monkeypatch.setattr(screen_capture, 'run_checked', lambda *args, **kwargs: 'ddagrab gdigrab')
    monkeypatch.setattr(screen_capture, 'primary_output', lambda _: 0)
    assert screen_capture.choose_capture(30, geometry) == 0
    assert 'seer-capture' not in tools


def test_selected_window_uses_raw_frames_and_never_desktop_fallback(monkeypatch):
    geometry = WindowRegion((0, 0, 640, 480), (Window(120, 42, 1234, 'Game'),))
    monkeypatch.setattr(sources, 'validate', lambda _: None)
    tools = []
    monkeypatch.setattr(screen_capture, 'executable', lambda name: tools.append(name) or name)
    assert screen_capture.choose_capture(30, geometry) == 'windows'
    args = screen_capture.input_args(30, geometry, 'windows')
    assert 'pipe:0' in args and 'rawvideo' in args
    assert 'desktop' not in args and 'gdigrab' not in args
    assert tools == ['seer-capture']


def test_parent_and_child_audio_are_not_captured_twice():
    assert root_process_ids((1, 2, 2, 3), {1: 0, 2: 1, 3: 0}) == (1, 3)
    assert root_process_ids((1, 9), {1: 0, 9: 8, 8: 1}) == (1,)


def test_source_lists_scroll_in_primary_mode_and_allow_more_sources(tmp_path, monkeypatch):
    from PySide6.QtWidgets import QApplication
    from PySide6.QtCore import Qt
    from app import source_dialog as module
    app = QApplication.instance() or QApplication([])
    available = [Window(i + 1, i + 10, i + 100, f'Window {i}') for i in range(30)]
    monkeypatch.setattr(module, 'windows', lambda _: available)
    monkeypatch.setattr(module, 'validate', lambda *args, **kwargs: None)
    monkeypatch.setattr(module, 'executable', lambda _: 'native')
    settings = QSettings(str(tmp_path / 'many-sources.ini'), QSettings.Format.IniFormat)
    dialog = module.SourceDialog(settings, [])
    dialog.show()
    app.processEvents()
    assert dialog.video_mode.currentIndex() == 0
    assert dialog.video_windows.isEnabled() and dialog.audio_windows.isEnabled()
    dialog.video_windows.scrollToBottom()
    assert dialog.video_windows.verticalScrollBar().value() > 0
    dialog.video_mode.setCurrentIndex(1)
    dialog.audio_mode.setCurrentIndex(dialog.audio_mode.findData('apps'))
    for index in range(5):
        dialog.video_windows.item(index).setCheckState(Qt.CheckState.Checked)
    for index in range(9):
        dialog.audio_windows.item(index).setCheckState(Qt.CheckState.Checked)
    dialog.save()
    assert len(load_selection(settings, 'sources/video')) == 5
    assert len(load_selection(settings, 'sources/audio')) == 9
    dialog.deleteLater()
    app.processEvents()
