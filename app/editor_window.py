from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QRectF, QStandardPaths, Qt, QUrl, Signal
from PySide6.QtGui import QImage
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer, QVideoFrame
from PySide6.QtWidgets import (QAbstractSpinBox, QDoubleSpinBox, QFileDialog, QHBoxLayout, QLabel,
    QMenu, QMessageBox, QProgressBar, QPushButton, QSlider, QVBoxLayout, QWidget)

from .export_dialog import ExportSettings
from .editor_state import EditorState
from .exporter import ExportOptions, ExportTarget, export_args
from .jobs import FFmpegJob, background
from .preview import Preview
from .video_utils import Media, probe
from .timing import clock, seconds_label


class Editor(QWidget):
    idle = Signal()
    saved = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.media: Media | None = None
        self.target = None
        self.exporting = self.loading = False
        self.temporary_source = False
        self.pending_position = None
        self.valid_range = (0.0, 1.0)
        self.player = QMediaPlayer(self)
        self.audio = QAudioOutput(self)
        self.player.setAudioOutput(self.audio)
        self.preview = Preview()
        self.preview.cropped = True
        self.preview.editable = False
        self.player.setVideoSink(self.preview.sink)
        self.player.positionChanged.connect(self._position)
        self.player.playbackStateChanged.connect(self._play_state)
        self.player.errorOccurred.connect(self._play_error)
        self.player.mediaStatusChanged.connect(self._media_status)
        self.job = FFmpegJob(self)
        self.job.progress.connect(lambda p: self.progress.setValue(round(p * 100)))
        self.job.done.connect(self._success)
        self.job.failed.connect(self._failure)
        self.options = ExportSettings(self)
        layout = QVBoxLayout(self)
        layout.addWidget(self.preview, 1)
        playback = QHBoxLayout()
        self.seek = QSlider(Qt.Orientation.Horizontal)
        self.seek.sliderMoved.connect(self.player.setPosition)
        playback.addWidget(self.seek, 1)
        self.time_label = QLabel('0s/0s')
        self.time_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        playback.addWidget(self.time_label)
        layout.addLayout(playback)
        times = QHBoxLayout()
        self.start, self.end = QDoubleSpinBox(), QDoubleSpinBox()
        for label, spin in (('Start', self.start), ('End', self.end)):
            spin.setDecimals(3)
            spin.setSuffix(' s')
            spin.setSingleStep(0.1)
            spin.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
            spin.setKeyboardTracking(False)
            times.addWidget(QLabel(label))
            times.addWidget(spin)
        self.start.valueChanged.connect(self._trim_changed)
        self.end.valueChanged.connect(self._trim_changed)
        layout.addLayout(times)
        buttons = QHBoxLayout()
        self.play = QPushButton('Play')
        self.play.clicked.connect(self._toggle)
        buttons.addWidget(self.play)
        self.crop = QPushButton('Crop')
        self.crop.setCheckable(True)
        self.crop.toggled.connect(self._crop_mode)
        buttons.addWidget(self.crop)
        self.reset = QPushButton('Reset')
        self.reset.clicked.connect(self.preview.reset_crop)
        self.reset.hide()
        buttons.addWidget(self.reset)
        buttons.addStretch()
        self.export_button = QPushButton('Export…')
        self.export_button.clicked.connect(self._export)
        self.export_button.setToolTip('Save the clip. Right-click for format, frame rate, and audio options.')
        self.export_button.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.export_button.customContextMenuRequested.connect(self._export_menu)
        buttons.addWidget(self.export_button)
        layout.addLayout(buttons)
        progress_row = QHBoxLayout()
        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        self.progress.hide()
        self.cancel = QPushButton('Cancel')
        self.cancel.clicked.connect(self.job.cancel)
        self.cancel.hide()
        progress_row.addWidget(self.progress, 1)
        progress_row.addWidget(self.cancel)
        layout.addLayout(progress_row)
        self.status = QLabel('')
        self.status.setWordWrap(True)
        self.status.hide()
        layout.addWidget(self.status)

    def message(self, text):
        self.status.setText(text)
        self.status.setVisible(bool(text))

    def load(self, path: Path, temporary: bool = False):
        self.temporary_source = temporary
        self.player.stop()
        self.player.setSource(QUrl())
        self.loading = True
        self.media = None
        self.pending_position = None
        self.setEnabled(False)
        self.message('Opening recording…')
        self.worker = background(probe, (path,), self._loaded, self._load_failed)

    def snapshot(self) -> EditorState | None:
        if self.media is None:
            return None
        rect = self.preview.crop
        return EditorState(self.media, self.temporary_source, self.start.value(), self.end.value(),
            self.player.position(), (rect.x(), rect.y(), rect.width(), rect.height()),
            self.options.format.currentData(), self.options.fps.currentData(),
            self.options.include_audio.isChecked())

    def restore(self, state: EditorState):
        self.temporary_source = state.temporary_source
        self._loaded(state.media)
        for spin, value in ((self.start, state.start), (self.end, state.end)):
            spin.blockSignals(True)
            spin.setValue(value)
            spin.blockSignals(False)
        self.valid_range = (state.start, state.end)
        self.preview.crop = QRectF(*state.crop)
        self.preview.update()
        self.options.format.setCurrentIndex(self.options.format.findData(state.format))
        self.options.fps.setCurrentIndex(self.options.fps.findData(state.fps))
        self.options.include_audio.setChecked(state.include_audio)
        self.pending_position = state.position
        self.player.setPosition(state.position)
        self._position(state.position)

    def _media_status(self, status):
        if status in (QMediaPlayer.MediaStatus.LoadedMedia, QMediaPlayer.MediaStatus.BufferedMedia) and self.pending_position is not None:
            position, self.pending_position = self.pending_position, None
            self.player.setPosition(position)

    def release_playback(self):
        self.player.stop()
        self.player.setSource(QUrl())
        self.player.setVideoSink(None)
        self.player.setAudioOutput(None)
        self.preview.sink.setVideoFrame(QVideoFrame())
        self.preview.image = QImage()

    def _loaded(self, media):
        self.loading = False
        self.setEnabled(True)
        self.media = media
        self.crop.setChecked(False)
        self.preview.set_source(media.width, media.height)
        for spin in (self.start, self.end):
            spin.blockSignals(True)
            spin.setRange(0, media.duration)
        self.start.setValue(0)
        self.end.setValue(int(media.duration * 1000) / 1000)
        self.valid_range = (self.start.value(), self.end.value())
        for spin in (self.start, self.end):
            spin.blockSignals(False)
        self.seek.setRange(0, round(media.duration * 1000))
        self.seek.setValue(0)
        self._position(0)
        self.options.set_audio(media.has_audio)
        self.player.setSource(QUrl.fromLocalFile(str(media.path)))
        self.player.pause()
        self.message('')
        self.idle.emit()

    def _load_failed(self, message):
        self.loading = False
        self.setEnabled(True)
        self.message('Could not open video: ' + message)
        self.idle.emit()

    def _trim_changed(self):
        start, end = self.start.value(), self.end.value()
        if end <= start:
            for spin, value in zip((self.start, self.end), self.valid_range):
                spin.blockSignals(True)
                spin.setValue(value)
                spin.blockSignals(False)
            return
        self.valid_range = (start, end)
        self.player.pause()
        position = start if self.sender() is self.start else end
        self.player.setPosition(round(position * 1000))

    def _position(self, milliseconds):
        if not self.seek.isSliderDown():
            self.seek.setValue(milliseconds)
        self.seek.setToolTip(clock(milliseconds / 1000))
        total = self.media.duration if self.media else 0
        position = min(total, max(0, milliseconds / 1000))
        self.time_label.setText(f'{seconds_label(position)}/{seconds_label(total)}')
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState and milliseconds / 1000 >= self.end.value():
            self.player.pause()

    def _toggle(self):
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
        else:
            if not self.start.value() <= self.player.position() / 1000 < self.end.value():
                self.player.setPosition(round(self.start.value() * 1000))
            self.player.play()

    def _play_state(self, state):
        self.play.setText('Pause' if state == QMediaPlayer.PlaybackState.PlayingState else 'Play')

    def _play_error(self, error, message):
        if error != QMediaPlayer.Error.NoError:
            self.message('Preview unavailable: ' + message)

    def _crop_mode(self, editing):
        self.preview.editable = editing
        self.preview.cropped = not editing
        self.preview.update()
        self.crop.setText('Done cropping' if editing else 'Crop')
        self.reset.setVisible(editing)

    def _export_menu(self, position):
        menu = QMenu(self)
        menu.addAction('Export options…', self.options.exec)
        menu.exec(self.export_button.mapToGlobal(position))

    def _export(self):
        if not self.media or self.exporting:
            return
        fmt = self.options.format.currentData()
        folder = self.media.path.parent
        if self.temporary_source:
            movies = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.MoviesLocation)
            folder = Path(movies) if movies and Path(movies).is_dir() else Path.home()
        name, _ = QFileDialog.getSaveFileName(self, 'Save clip',
            str(folder / (self.media.path.stem + '-edited.' + fmt)), f'{fmt.upper()} (*.{fmt})',
            options=QFileDialog.Option.DontConfirmOverwrite)
        if not name:
            return
        destination = Path(name)
        if destination.suffix.lower() != '.' + fmt:
            destination = destination.with_suffix('.' + fmt)
        overwrite = destination.exists()
        if overwrite and QMessageBox.question(self, 'Replace file?', f'Replace {destination.name}?') != QMessageBox.StandardButton.Yes:
            return
        try:
            options = ExportOptions(fmt, self.start.value(), self.end.value(), self.preview.source_crop(),
                                    self.options.fps.currentData(), self.options.include_audio.isChecked())
            self.target = ExportTarget(destination, self.media.path, overwrite)
            args = export_args(self.media, options, self.target.temporary)
            self.player.pause()
            self.crop.setChecked(False)
            self._export_busy(True)
            self.message('Exporting…')
            self.job.start(args, options.end - options.start)
        except Exception as error:
            self._failure(str(error))

    def _export_busy(self, busy):
        self.exporting = busy
        for widget in (self.play, self.crop, self.reset, self.export_button, self.seek,
                       self.start, self.end, self.preview):
            widget.setEnabled(not busy)
        self.progress.setVisible(busy)
        self.progress.setValue(0)
        self.cancel.setVisible(busy)

    def _success(self):
        try:
            self.target.commit()
            self.saved.emit(self.target.destination)
            self.message('Saved to ' + str(self.target.destination))
        except Exception as error:
            self._failure(str(error))
            return
        self.target = None
        self._export_busy(False)
        self.idle.emit()

    def _failure(self, message):
        if self.target:
            self.target.cleanup()
            self.target = None
        self._export_busy(False)
        self.message(message)
        self.idle.emit()
