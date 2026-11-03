from __future__ import annotations

import os
from dataclasses import replace
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import pytest
from PySide6.QtCore import QPoint, QRectF, Qt
from PySide6.QtGui import QColor, QImage
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QAbstractSpinBox

from app.editor_window import Editor
from app.video_utils import Media


@pytest.fixture(scope='module')
def application():
    return QApplication.instance() or QApplication([])


def test_format_controls_and_missing_audio(application, tmp_path):
    editor = Editor()
    media = Media(tmp_path / 'silent.mp4', 160, 120, 3, False, '30/1', '30/1', '1/1000', 0, (), False)
    editor._loaded(media)
    editor.show()
    application.processEvents()
    options = editor.options
    assert not options.isVisible()
    assert options.fps.currentText() == 'Same as source'
    assert not options.include_audio.isEnabled()
    assert not options.format.model().item(options.format.findData('mp3')).isEnabled()
    assert 'No audio track' in options.note.text()
    media = replace(media, has_audio=True)
    editor._loaded(media)
    options.show()
    options.format.setCurrentIndex(options.format.findData('wav'))
    application.processEvents()
    assert not options.fps.isVisible()
    assert not options.include_audio.isVisible()
    options.format.setCurrentIndex(options.format.findData('gif'))
    application.processEvents()
    assert options.fps.isVisible()
    assert not options.include_audio.isVisible()
    options.close()
    editor.player.stop()
    editor.close()


def test_crop_mouse_interaction(application):
    from app.preview import Preview
    preview = Preview()
    preview.resize(800, 600)
    preview.set_source(1920, 1080)
    preview.show()
    application.processEvents()
    top_left = preview.selection().topLeft().toPoint() + QPoint(2, 2)
    QTest.mousePress(preview, Qt.MouseButton.LeftButton, pos=top_left)
    QTest.mouseMove(preview, top_left + QPoint(100, 70))
    QTest.mouseRelease(preview, Qt.MouseButton.LeftButton)
    crop = preview.source_crop()
    assert crop.x > 200 and crop.y > 100
    crop.validate(1920, 1080)
    preview.reset_crop()
    assert preview.source_crop().width == 1920
    preview.close()


def test_simple_editor_trim_fields_and_actual_crop_preview(application, tmp_path):
    editor = Editor()
    media = Media(tmp_path / 'clip.mp4', 100, 100, 10, False, '30/1', '30/1', '1/1000', 0, (), False)
    editor._loaded(media)
    editor.resize(600, 440)
    editor.show()
    application.processEvents()
    assert editor.start.buttonSymbols() == QAbstractSpinBox.ButtonSymbols.NoButtons
    assert editor.end.buttonSymbols() == QAbstractSpinBox.ButtonSymbols.NoButtons
    editor._position(800)
    assert editor.time_label.text() == '0.8s/10s'
    editor._position(1250)
    assert editor.time_label.text() == '1.25s/10s'
    editor.start.setValue(2.5)
    editor.end.setValue(7.25)
    assert editor.valid_range == (2.5, 7.25)
    editor.start.setValue(8)
    assert editor.start.value() == 2.5
    assert not editor.options.isVisible()
    assert not editor.reset.isVisible()
    editor.crop.click()
    assert editor.preview.editable and not editor.preview.cropped
    image = QImage(100, 100, QImage.Format.Format_RGB32)
    image.fill(QColor('red'))
    for x in range(50, 100):
        for y in range(100):
            image.setPixelColor(x, y, QColor('green'))
    editor.preview.image = image
    editor.preview.crop = QRectF(0.5, 0, 0.5, 1)
    editor.crop.click()
    assert editor.preview.cropped and not editor.preview.editable
    result = editor.preview.grab().toImage()
    assert result.pixelColor(result.width() // 2, result.height() // 2) == QColor('green')
    assert editor.preview.source_crop().width == 50
    editor.player.stop()
    editor.close()


def test_close_during_capture_preparation_releases_busy_state(application, monkeypatch):
    from app import recorder as module
    recorder = module.Recorder()
    messages = []
    recorder.failed.connect(messages.append)
    recorder.busy = True
    recorder.stop()
    assert recorder.stopping
    assert not recorder.watchdog.isActive()

    def immediate(function, args=(), result=None, error=None):
        value = function(*args)
        if result:
            result(value)

    monkeypatch.setattr(module, 'background', immediate)
    recorder._launch(None)
    assert not recorder.busy
    assert messages == ['Recording cancelled before capture began.']


def test_completed_export_survives_temporary_recording_cleanup(application, tmp_path, monkeypatch):
    from app.exporter import ExportTarget
    from app.sessions import RecordingSessions
    folder = tmp_path / 'session'
    folder.mkdir()
    monkeypatch.setattr('app.sessions.tempfile.mkdtemp', lambda **kwargs: str(folder))
    sessions = RecordingSessions()
    sessions.create()
    source = folder / 'screen.mp4'
    source.write_bytes(b'original capture')
    destination = folder / 'recording.mp4'
    editor = Editor()
    editor.saved.connect(sessions.protect_export)
    editor.target = ExportTarget(destination, source)
    editor.target.temporary.write_bytes(b'completed export')
    editor._success()
    assert sessions.cleanup() == []
    assert not source.exists()
    assert destination.read_bytes() == b'completed export'
    editor.close()


def test_snapshot_preserves_edits_after_editor_destruction(application, tmp_path):
    from PySide6.QtCore import QCoreApplication, QEvent
    from shiboken6 import isValid
    editor = Editor()
    media = Media(tmp_path / 'clip.mp4', 1920, 1080, 10, True, '30/1', '30/1', '1/1000', 0, (), False)
    editor._loaded(media)
    editor.start.setValue(2)
    editor.end.setValue(8)
    editor.preview.crop = QRectF(0.1, 0.2, 0.7, 0.6)
    editor.options.format.setCurrentIndex(editor.options.format.findData('webm'))
    editor.options.fps.setCurrentIndex(editor.options.fps.findData(24))
    editor.options.include_audio.setChecked(False)
    state = editor.snapshot()
    old_player, old_sink = editor.player, editor.preview.sink
    editor.release_playback()
    editor.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert not isValid(old_player) and not isValid(old_sink)
    restored = Editor()
    restored.restore(state)
    assert restored.start.value() == 2 and restored.end.value() == 8
    assert restored.preview.crop == QRectF(0.1, 0.2, 0.7, 0.6)
    assert restored.options.format.currentData() == 'webm'
    assert restored.options.fps.currentData() == 24
    assert not restored.options.include_audio.isChecked()
    restored.release_playback()
    restored.deleteLater()
