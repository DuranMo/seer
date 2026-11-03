from __future__ import annotations

from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox,
    QFormLayout, QLabel)

from .exporter import AUDIO_FORMATS, VIDEO_FORMATS, RATES


class ExportSettings(QDialog):
    """Occasional export choices, kept out of the small editor."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle('Export options')
        layout = QFormLayout(self)
        self.format = QComboBox()
        for fmt in VIDEO_FORMATS + AUDIO_FORMATS:
            self.format.addItem(fmt.upper(), fmt)
        layout.addRow('Format', self.format)
        self.fps = QComboBox()
        self.fps.addItem('Same as source', None)
        for rate in RATES:
            self.fps.addItem(f'{rate} FPS', rate)
        self.fps_label = QLabel('Frame rate')
        layout.addRow(self.fps_label, self.fps)
        self.include_audio = QCheckBox('Include audio')
        self.include_audio.setChecked(True)
        layout.addRow(self.include_audio)
        self.note = QLabel()
        layout.addRow(self.note)
        done = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        done.rejected.connect(self.accept)
        layout.addRow(done)
        self.has_audio = False
        self.format.currentIndexChanged.connect(self.refresh)
        self.set_audio(False)

    def set_audio(self, available: bool):
        self.has_audio = available
        for fmt in AUDIO_FORMATS:
            item = self.format.model().item(self.format.findData(fmt))
            item.setEnabled(available)
            item.setToolTip('' if available else 'This recording has no audio track.')
        if not available and self.format.currentData() in AUDIO_FORMATS:
            self.format.setCurrentIndex(0)
        self.include_audio.setEnabled(available)
        self.include_audio.setChecked(available)
        self.note.setText('' if available else 'No audio track; audio-only export is unavailable.')
        self.refresh()

    def refresh(self):
        fmt = self.format.currentData()
        video = fmt in VIDEO_FORMATS
        self.fps.setVisible(video)
        self.fps_label.setVisible(video)
        self.include_audio.setVisible(video and fmt != 'gif')
