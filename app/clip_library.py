"""On-demand clip library with small cached thumbnails and no media player."""
from __future__ import annotations

from pathlib import Path
from datetime import datetime

from PySide6.QtCore import Qt, Signal, QSize, QTimer, QItemSelectionModel
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (QDialog, QHBoxLayout, QLabel, QListWidget,
    QListWidgetItem, QPlainTextEdit, QPushButton, QVBoxLayout, QLineEdit, QMessageBox)

from .thumbnails import Thumbnails, cache_path
from .clip_storage import read_description, save_description, rename_clip, delete_clip, migrate_metadata
from .exporter import STAGING_PREFIX

VIDEO_SUFFIXES = {'.mp4', '.mkv', '.mov', '.webm', '.avi', '.m4v'}


def clip_label(path: Path):
    title = path.name
    try:
        stat = path.stat()
        return title + '\n' + f'{stat.st_size / 1048576:.1f} MB · {datetime.fromtimestamp(stat.st_mtime):%b %d}'
    except OSError:
        return title




class ClipLibrary(QDialog):
    open_clip = Signal(object)

    def __init__(self, folder: Path, parent=None):
        super().__init__(parent)
        self.setWindowFlag(Qt.WindowType.WindowMinMaxButtonsHint, True)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setWindowTitle('Clips')
        self.resize(940, 680)
        self.folder, self.current = folder, None
        self.original = ''
        self.original_title = ''
        self.selection_snapshot = ()
        self.deleting = False
        self.rename_handler = rename_clip
        self.delete_handler = delete_clip
        layout = QVBoxLayout(self)
        self.list = QListWidget()
        self.list.setViewMode(QListWidget.ViewMode.IconMode)
        self.list.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        self.list.setToolTip('Ctrl-click to select clips; Shift-click for a range; Ctrl+A selects all.')
        self.list.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.list.setMovement(QListWidget.Movement.Static)
        self.list.setWrapping(True)
        self.list.setWordWrap(True)
        self.list.setIconSize(QSize(240, 135))
        self.list.setGridSize(QSize(268, 192))
        self.list.setSpacing(8)
        self.list.setUniformItemSizes(True)
        self.thumbnails = Thumbnails(self)
        self.thumbnails.ready.connect(self._thumbnail_ready)
        self.thumbnails.idle.connect(self._queue_thumbnails)
        self.thumbnail_timer = QTimer(self)
        self.thumbnail_timer.setSingleShot(True)
        self.thumbnail_timer.timeout.connect(self._load_thumbnails)
        self.list.verticalScrollBar().valueChanged.connect(self._queue_thumbnails)
        self.thumbnail_items = {}
        layout.addWidget(self.list)
        layout.addWidget(QLabel('Title / filename'))
        self.title = QLineEdit()
        self.title.setEnabled(False)
        layout.addWidget(self.title)
        layout.addWidget(QLabel('Description'))
        self.description = QPlainTextEdit()
        self.description.setMaximumHeight(85)
        self.description.setEnabled(False)
        layout.addWidget(self.description)
        self.message = QLabel('')
        self.message.setWordWrap(True)
        layout.addWidget(self.message)
        row = QHBoxLayout()
        self.save_button = QPushButton('Save')
        self.delete_button = QPushButton('Delete…')
        self.open_button = QPushButton('Open in editor')
        refresh = QPushButton('Refresh')
        row.addWidget(self.save_button)
        row.addWidget(self.delete_button)
        row.addStretch()
        row.addWidget(refresh)
        row.addWidget(self.open_button)
        layout.addLayout(row)
        self.save_button.clicked.connect(self.save)
        self.delete_button.clicked.connect(self.delete_selected)
        self.open_button.clicked.connect(self.open_selected)
        refresh.clicked.connect(lambda: self.refresh())
        self.list.itemSelectionChanged.connect(self._selection_changed)
        self.list.itemDoubleClicked.connect(lambda _: self.open_selected())
        self.refresh()

    def save(self) -> bool:
        if self.deleting:
            self.message.setText('Finish deleting clips first.')
            return False
        if self.current is None or (self.description.toPlainText() == self.original
                                    and self.title.text() == self.original_title):
            return True
        try:
            old = self.current
            if self.title.text() != self.original_title:
                self.current = self.rename_handler(old, self.title.text())
                item = self.thumbnail_items.pop(old)
                item.setData(Qt.ItemDataRole.UserRole, self.current)
                item.setToolTip(str(self.current))
                self.thumbnail_items[self.current] = item
                self.selection_snapshot = tuple(self.current if path == old else path for path in self.selection_snapshot)
                self.original_title = self.current.name
            save_description(self.current, self.description.toPlainText())
            self.original = self.description.toPlainText()
            item = self.thumbnail_items.get(self.current)
            if item is not None:
                item.setText(clip_label(self.current))
            self.message.setText('Saved.')
            return True
        except (OSError, ValueError) as error:
            self.message.setText(f'Could not save clip details: {error}')
            return False

    def selected_paths(self):
        return tuple(item.data(Qt.ItemDataRole.UserRole)
                     for item in sorted(self.list.selectedItems(), key=self.list.row))

    def _selection_changed(self):
        if not self.save():
            self.list.blockSignals(True)
            self.list.clearSelection()
            for path in self.selection_snapshot:
                item = self.thumbnail_items.get(path)
                if item is not None:
                    item.setSelected(True)
            if self.current in self.thumbnail_items:
                self.list.setCurrentItem(self.thumbnail_items[self.current], QItemSelectionModel.SelectionFlag.NoUpdate)
            self.list.blockSignals(False)
            return
        selected = self.selected_paths()
        self.selection_snapshot = selected
        self.current = selected[0] if len(selected) == 1 else None
        self.original = read_description(self.current) if self.current else ''
        self.original_title = self.current.name if self.current else ''
        self.title.setText(self.original_title)
        self.title.setEnabled(self.current is not None)
        self.description.setPlainText(self.original)
        self.description.setEnabled(self.current is not None)
        self.open_button.setEnabled(self.current is not None)
        self.save_button.setEnabled(self.current is not None)
        self.delete_button.setEnabled(bool(selected))
        self.delete_button.setText(f'Delete {len(selected)} clips…' if len(selected) > 1 else 'Delete…')
        if len(selected) > 1:
            self.message.setText(f'{len(selected)} clips selected.')
        elif self.message.text().endswith('clips selected.'):
            self.message.setText('')

    def refresh(self, *, selection=None):
        if self.deleting:
            return
        if not self.save():
            return
        selected = self.selected_paths() if selection is None else tuple(selection)
        self.list.blockSignals(True)
        self.thumbnail_items.clear()
        self.list.clear()
        self.current = None
        self.selection_snapshot = ()
        self.original = ''
        self.original_title = ''
        self.title.clear()
        self.description.clear()
        try:
            migrate_metadata(self.folder)
            paths = sorted((p for p in self.folder.iterdir() if p.is_file()
                            and p.suffix.lower() in VIDEO_SUFFIXES
                            and not p.name.startswith(STAGING_PREFIX)),
                           key=lambda p: p.stat().st_mtime, reverse=True)
            self.message.setText('' if paths else 'No clips yet. Saved clips appear here.')
        except OSError as error:
            paths = []
            self.message.setText(f'Could not read clips: {error}')
        for path in paths:
            item = QListWidgetItem(clip_label(path))
            item.setData(Qt.ItemDataRole.UserRole, path)
            item.setToolTip(str(path))
            item.setSizeHint(QSize(252, 178))
            self.list.addItem(item)
            self.thumbnail_items[path] = item
        restored = [self.thumbnail_items[path] for path in selected if path in self.thumbnail_items]
        if restored:
            for item in restored:
                item.setSelected(True)
            self.list.setCurrentItem(restored[0], QItemSelectionModel.SelectionFlag.NoUpdate)
        elif paths:
            self.list.setCurrentRow(0)
        self.list.blockSignals(False)
        self._selection_changed()
        self._queue_thumbnails()

    def _queue_thumbnails(self, *_):
        if self.isVisible():
            self.thumbnail_timer.start(40)

    def _load_thumbnails(self):
        if not self.isVisible():
            return
        for row in range(self.list.count()):
            item = self.list.item(row)
            path = item.data(Qt.ItemDataRole.UserRole)
            if not item.icon().isNull():
                continue
            cached = self.thumbnails.request(path)
            if cached:
                item.setIcon(QIcon(str(cached)))

    def _thumbnail_ready(self, path, cached):
        item = self.thumbnail_items.get(path)
        if item is not None:
            item.setIcon(QIcon(str(cached)))

    def showEvent(self, event):
        super().showEvent(event)
        self._queue_thumbnails()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._queue_thumbnails()

    def open_selected(self):
        if self.current is not None and self.save():
            if self.current.name.startswith(STAGING_PREFIX):
                self.message.setText('This clip is still being saved.')
                return
            if not self.current.is_file():
                self.message.setText('This clip is no longer available. Click Refresh.')
                return
            self.open_clip.emit(self.current)

    def delete_selected(self):
        paths = self.selected_paths()
        if not paths or self.deleting:
            return
        if len(paths) == 1:
            question = f'Are you sure you want to permanently delete "{paths[0].name}"?\n\nIts description will also be deleted.'
        else:
            names = '\n'.join(path.name for path in paths[:5])
            if len(paths) > 5:
                names += f'\n…and {len(paths) - 5} more.'
            question = f'Are you sure you want to permanently delete {len(paths)} clips?\n\n{names}\n\nTheir descriptions will also be deleted.'
        answer = QMessageBox.question(self, 'Delete clip',
            question,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if answer != QMessageBox.StandardButton.Yes:
            return
        if self.selected_paths() != paths:
            self.message.setText('The selection changed. Select the clips and try again.')
            return
        deleted, failed, warnings = [], [], []
        errors = []
        self.deleting = True
        try:
            if self.thumbnails.current is not None and self.thumbnails.current[0] in paths:
                self.thumbnails.stop()
                self.thumbnail_timer.stop()
            for path in paths:
                try:
                    try:
                        cached = cache_path(path, self.thumbnails.cache)
                    except OSError:
                        cached = None
                    warnings.extend(self.delete_handler(path))
                    deleted.append(path)
                except OSError as error:
                    failed.append(path)
                    errors.append(f'{path.name}: {error}')
                    continue
                if cached is not None:
                    try:
                        cached.unlink(missing_ok=True)
                    except OSError as error:
                        warnings.append(str(error))
        finally:
            self.deleting = False
        if deleted:
            # Never autosave details for a file which has just been removed.
            self.current = None
            self.original = self.original_title = ''
            self.refresh(selection=failed)
        else:
            self._queue_thumbnails()
        if not deleted:
            message = 'Could not delete clips: ' + '; '.join(errors[:5])
        else:
            message = 'Clip deleted.' if len(paths) == 1 else f'Deleted {len(deleted)} of {len(paths)} clips.'
            if errors:
                message += ' Could not delete: ' + '; '.join(errors[:5])
        if warnings:
            message += ' Some associated files could not be removed: ' + '; '.join(warnings[:5])
        self.message.setText(message)

    def closeEvent(self, event):
        if self.save():
            self.thumbnail_timer.stop()
            self.thumbnails.stop()
            event.accept()
        else:
            event.ignore()

    def done(self, result):
        if self.save():
            self.thumbnail_timer.stop()
            self.thumbnails.stop()
            super().done(result)
