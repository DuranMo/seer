from __future__ import annotations

import sys
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QComboBox, QDialog, QDialogButtonBox, QLabel,
    QListWidget, QListWidgetItem, QVBoxLayout, QPushButton)

from .capture_sources import windows, load_selection, save_selection, validate
from .video_utils import executable


class SourceDialog(QDialog):
    def __init__(self, settings, devices, exclude=(), parent=None):
        super().__init__(parent)
        self.settings, self.exclude = settings, exclude
        self.setWindowTitle('Capture sources')
        self.resize(540, 650)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel('Video'))
        self.video_mode = QComboBox()
        self.video_mode.addItems(['All (primary display)', 'Selected windows'])
        layout.addWidget(self.video_mode)
        self.video_windows = QListWidget()
        layout.addWidget(self.video_windows)
        layout.addWidget(QLabel('Audio'))
        self.audio_mode = QComboBox()
        self.audio_mode.addItem('No audio', '')
        for device in devices:
            self.audio_mode.addItem(('All system audio — ' if device.mode == 'system' else 'Microphone — ') + device.name,
                                    device.mode + ':' + device.name)
        self.audio_mode.addItem('Selected apps (mixed into one track)', 'apps')
        layout.addWidget(self.audio_mode)
        self.audio_windows = QListWidget()
        layout.addWidget(self.audio_windows)
        note = QLabel('Multiple windows are tiled into one video; more sources use more resources. '
                      'App audio includes child processes. Restore minimized video windows before starting.')
        note.setWordWrap(True)
        layout.addWidget(note)
        self.error = QLabel('')
        self.error.setWordWrap(True)
        layout.addWidget(self.error)
        refresh = QPushButton('Refresh windows')
        refresh.clicked.connect(self.refresh)
        layout.addWidget(refresh)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.selected_video = load_selection(settings, 'sources/video', strict=False)
        self.selected_audio = load_selection(settings, 'sources/audio', strict=False)
        self.refresh()
        self.video_mode.setCurrentIndex(int(bool(self.selected_video)))
        self.audio_mode.setCurrentIndex(max(0, self.audio_mode.findData(str(settings.value('recording/audio', '')))))
        self.video_mode.currentIndexChanged.connect(self.sync)
        self.audio_mode.currentIndexChanged.connect(self.sync)
        self.sync()

    def sync(self, *_):
        for widget, editable in ((self.video_windows, self.video_mode.currentIndex() == 1),
                                 (self.audio_windows, self.audio_mode.currentData() == 'apps')):
            # Keep scrolling/selection available in All/Primary mode. Only
            # checking sources depends on the selected capture mode.
            widget.setEnabled(True)
            for index in range(widget.count()):
                item = widget.item(index)
                flags = item.flags()
                item.setFlags(flags | Qt.ItemFlag.ItemIsUserCheckable if editable else flags & ~Qt.ItemFlag.ItemIsUserCheckable)

    @staticmethod
    def checked(widget):
        return tuple(widget.item(i).data(Qt.ItemDataRole.UserRole) for i in range(widget.count())
                     if widget.item(i).checkState() == Qt.CheckState.Checked)

    def refresh(self):
        if self.video_windows.count():
            self.selected_video = self.checked(self.video_windows)
            self.selected_audio = self.checked(self.audio_windows)
        available = windows(self.exclude)
        for widget, selection in ((self.video_windows, self.selected_video), (self.audio_windows, self.selected_audio)):
            widget.clear()
            seen = set()
            for window in available:
                if widget is self.audio_windows and window.pid in seen:
                    continue
                seen.add(window.pid)
                item = QListWidgetItem(window.title + f' ({window.pid})')
                item.setData(Qt.ItemDataRole.UserRole, window)
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                match = any(w.pid == window.pid and w.created == window.created and
                            (widget is self.audio_windows or w.handle == window.handle) for w in selection)
                item.setCheckState(Qt.CheckState.Checked if match else Qt.CheckState.Unchecked)
                widget.addItem(item)
        self.sync()

    def save(self):
        try:
            selected = self.checked(self.video_windows) if self.video_mode.currentIndex() else ()
            apps = self.checked(self.audio_windows) if self.audio_mode.currentData() == 'apps' else ()
            if self.video_mode.currentIndex() and not selected:
                raise ValueError('Choose at least one video window.')
            if self.audio_mode.currentData() == 'apps' and not apps:
                raise ValueError('Choose at least one audio app.')
            if selected or apps:
                executable('seer-capture')
                if sys.getwindowsversion().build < (20348 if apps else 19041):
                    raise ValueError('This capture selection needs a newer Windows build. App audio requires build 20348 or later.')
            validate(selected)
            validate(apps, audio=True)
            save_selection(self.settings, 'sources/video', selected)
            save_selection(self.settings, 'sources/audio', apps)
            self.settings.setValue('recording/audio', self.audio_mode.currentData())
            self.settings.sync()
            self.accept()
        except (ValueError, RuntimeError, OSError) as error:
            self.error.setText(str(error))
