import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QColor, QImage

from app.clip_library import ClipLibrary
from app.clip_storage import description_path, read_description, save_description
from app.clip_library import clip_label
from app.clip_storage import rename_clip
from app.clip_storage import delete_clip
from PySide6.QtWidgets import QMessageBox
import pytest


def test_description_round_trip_does_not_modify_clip(tmp_path):
    clip = tmp_path / 'clip.mp4'
    clip.write_bytes(b'original media')
    save_description(clip, 'a good round\nsecond line')
    assert read_description(clip) == 'a good round\nsecond line'
    assert clip.read_bytes() == b'original media'
    assert not list(tmp_path.glob('*.tmp'))
    description_path(clip).write_text('broken json')
    assert read_description(clip) == ''


def test_description_saved_in_metadata_and_existing_sidecar_migrates(tmp_path):
    clip = tmp_path / 'clip.mp4'
    clip.write_bytes(b'video')
    legacy = tmp_path / 'clip.mp4.seer.json'
    legacy.write_text('{"description":"keep me","extra":42}', encoding='utf-8')
    assert read_description(clip) == 'keep me'
    assert not legacy.exists()
    assert description_path(clip) == tmp_path / 'metadata' / legacy.name
    save_description(clip, 'updated')
    import json
    assert json.loads(description_path(clip).read_text()) == {'description': 'updated', 'extra': 42}
    assert list(tmp_path.glob('*.json')) == []


def test_migration_preserves_conflicting_metadata_in_subfolder(tmp_path):
    clip = tmp_path / 'clip.mp4'
    clip.touch()
    save_description(clip, 'new version')
    legacy = tmp_path / 'clip.mp4.seer.json'
    legacy.write_text('{"description":"old version"}', encoding='utf-8')
    assert read_description(clip) == 'new version'
    assert not legacy.exists()
    backups = list((tmp_path / 'metadata').glob('*.legacy-*.json'))
    assert len(backups) == 1 and 'old version' in backups[0].read_text()


def test_manager_migrates_all_sidecars_without_selecting_every_clip(tmp_path):
    app = QApplication.instance() or QApplication([])
    for name in ('first.mp4', 'second.mp4'):
        (tmp_path / name).touch()
        (tmp_path / (name + '.seer.json')).write_text('{"description":"existing"}')
    unrelated = tmp_path / 'other.json'
    unrelated.write_text('{"keep":true}')
    library = ClipLibrary(tmp_path)
    assert len(list((tmp_path / 'metadata').glob('*.seer.json'))) == 2
    assert not list(tmp_path.glob('*.seer.json'))
    assert unrelated.read_text() == '{"keep":true}'
    library.close()
    app.processEvents()


def test_title_renames_video_and_description_without_using_description_as_title(tmp_path):
    app = QApplication.instance() or QApplication([])
    clip = tmp_path / 'original.mp4'
    clip.write_bytes(b'original video')
    save_description(clip, 'a description that is not a title')
    library = ClipLibrary(tmp_path)
    assert library.title.text() == 'original.mp4'
    assert clip_label(clip).startswith('original.mp4\n')
    library.title.setText('renamed.mp4')
    library.description.setPlainText('new independent description')
    assert library.save()
    renamed = tmp_path / 'renamed.mp4'
    assert not clip.exists() and not description_path(clip).exists()
    assert renamed.read_bytes() == b'original video'
    assert read_description(renamed) == 'new independent description'
    assert library.current == renamed
    assert library.list.currentItem().text().startswith('renamed.mp4\n')
    library.close()
    app.processEvents()


@pytest.mark.parametrize('name', ['existing.mp4', '../escape.mp4', 'CON.mp4', 'clip.wav'])
def test_rename_rejects_collisions_paths_reserved_names_and_format_changes(tmp_path, name):
    original, existing = tmp_path / 'original.mp4', tmp_path / 'existing.mp4'
    original.write_bytes(b'original')
    existing.write_bytes(b'existing')
    save_description(original, 'preserved')
    with pytest.raises((ValueError, OSError)):
        rename_clip(original, name)
    assert original.read_bytes() == b'original' and existing.read_bytes() == b'existing'
    assert read_description(original) == 'preserved'


def test_sidecar_failure_rolls_back_video_rename(tmp_path, monkeypatch):
    from pathlib import Path
    original = tmp_path / 'original.mp4'
    original.write_bytes(b'original video')
    save_description(original, 'original description')
    actual_rename = Path.rename
    def fail_sidecar(path, target):
        if path == description_path(original):
            raise PermissionError('Description is locked')
        return actual_rename(path, target)
    monkeypatch.setattr(Path, 'rename', fail_sidecar)
    with pytest.raises(PermissionError):
        rename_clip(original, 'new.mp4')
    assert original.read_bytes() == b'original video'
    assert read_description(original) == 'original description'
    assert not (tmp_path / 'new.mp4').exists()


def test_delete_requires_confirmation_and_cancel_preserves_pending_edits(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    clip = tmp_path / 'clip.mp4'
    clip.write_bytes(b'video')
    save_description(clip, 'saved description')
    library = ClipLibrary(tmp_path)
    library.thumbnails.cache = tmp_path / 'cache'
    library.title.setText('unsaved.mp4')
    library.description.setPlainText('unsaved description')
    prompts = []
    def cancel(parent, title, message, buttons, default):
        prompts.append((message, default))
        return QMessageBox.StandardButton.No
    monkeypatch.setattr(QMessageBox, 'question', cancel)
    library.delete_selected()
    assert clip.exists() and read_description(clip) == 'saved description'
    assert library.title.text() == 'unsaved.mp4'
    assert library.description.toPlainText() == 'unsaved description'
    assert 'clip.mp4' in prompts[0][0] and 'permanently delete' in prompts[0][0]
    assert prompts[0][1] == QMessageBox.StandardButton.No
    library.title.setText('clip.mp4')
    library.description.setPlainText('saved description')
    library.close()
    app.processEvents()


def test_confirmed_delete_removes_video_metadata_and_library_entry(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    clip = tmp_path / 'clip.mp4'
    clip.write_bytes(b'video')
    save_description(clip, 'description')
    unrelated = tmp_path / 'metadata' / 'other.mp4.seer.json'
    unrelated.write_text('keep this')
    library = ClipLibrary(tmp_path)
    library.thumbnails.cache = tmp_path / 'cache'
    library.description.setPlainText('unsaved edit should not recreate the deleted sidecar')
    monkeypatch.setattr(QMessageBox, 'question', lambda *args: QMessageBox.StandardButton.Yes)
    library.delete_selected()
    assert not clip.exists() and not description_path(clip).exists()
    assert unrelated.read_text() == 'keep this'
    assert library.list.count() == 0 and library.current is None
    assert not library.delete_button.isEnabled()
    library.close()
    app.processEvents()
    assert not description_path(clip).exists()


def test_locked_video_keeps_metadata_and_selection(tmp_path, monkeypatch):
    from pathlib import Path
    app = QApplication.instance() or QApplication([])
    clip = tmp_path / 'clip.mp4'
    clip.write_bytes(b'video')
    save_description(clip, 'description')
    library = ClipLibrary(tmp_path)
    library.thumbnails.cache = tmp_path / 'cache'
    original = Path.unlink
    def locked(path, *args, **kwargs):
        if path == clip:
            raise PermissionError('Video is locked')
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'unlink', locked)
    monkeypatch.setattr(QMessageBox, 'question', lambda *args: QMessageBox.StandardButton.Yes)
    library.delete_selected()
    assert clip.exists() and read_description(clip) == 'description'
    assert library.current == clip and 'locked' in library.message.text()
    library.close()
    app.processEvents()


def test_metadata_cleanup_failure_reports_video_is_already_deleted(tmp_path, monkeypatch):
    from pathlib import Path
    clip = tmp_path / 'clip.mp4'
    clip.write_bytes(b'video')
    save_description(clip, 'description')
    original = Path.unlink
    def locked(path, *args, **kwargs):
        if path == description_path(clip):
            raise PermissionError('Metadata is locked')
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'unlink', locked)
    warnings = delete_clip(clip)
    assert not clip.exists() and description_path(clip).exists()
    assert len(warnings) == 1 and 'locked' in warnings[0]


def test_multi_selection_saves_single_clip_edits_and_survives_refresh(tmp_path):
    app = QApplication.instance() or QApplication([])
    for name in ('one.mp4', 'two.mp4', 'three.mp4'):
        (tmp_path / name).write_bytes(b'video')
    library = ClipLibrary(tmp_path)
    original = library.current
    library.description.setPlainText('keep this edit')
    library.list.selectAll()
    assert read_description(original) == 'keep this edit'
    selected = library.selected_paths()
    assert len(selected) == 3 and library.current is None
    assert not library.title.isEnabled() and not library.description.isEnabled()
    assert not library.open_button.isEnabled() and not library.save_button.isEnabled()
    assert library.delete_button.isEnabled() and '3' in library.delete_button.text()
    library.refresh()
    assert library.selected_paths() == selected
    library.close()
    app.processEvents()


@pytest.mark.parametrize('confirm', [False, True])
def test_bulk_delete_confirms_once_and_only_deletes_selected_files(tmp_path, monkeypatch, confirm):
    app = QApplication.instance() or QApplication([])
    paths = [tmp_path / f'{name}.mp4' for name in ('one', 'two', 'keep')]
    for path in paths:
        path.write_bytes(b'video')
        save_description(path, path.stem)
    library = ClipLibrary(tmp_path)
    library.thumbnails.cache = tmp_path / 'cache'
    library.list.clearSelection()
    for path in paths[:2]:
        library.thumbnail_items[path].setSelected(True)
    prompts = []
    def answer(parent, title, message, buttons, default):
        prompts.append((message, default))
        return QMessageBox.StandardButton.Yes if confirm else QMessageBox.StandardButton.No
    monkeypatch.setattr(QMessageBox, 'question', answer)
    library.delete_selected()
    assert len(prompts) == 1 and '2 clips' in prompts[0][0]
    assert prompts[0][1] == QMessageBox.StandardButton.No
    for path in paths[:2]:
        assert path.exists() == (not confirm)
        assert description_path(path).exists() == (not confirm)
    assert paths[2].exists() and read_description(paths[2]) == 'keep'
    assert library.list.count() == (1 if confirm else 3)
    library.close()
    app.processEvents()


def test_bulk_delete_continues_after_locked_clip_and_keeps_failed_clip_selected(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    locked, removable = tmp_path / 'locked.mp4', tmp_path / 'removable.mp4'
    for path in (locked, removable):
        path.write_bytes(b'video')
        save_description(path, 'preserved' if path == locked else 'remove')
    library = ClipLibrary(tmp_path)
    library.thumbnails.cache = tmp_path / 'cache'
    library.list.selectAll()
    calls = []
    def remove(path):
        calls.append(path)
        if path == locked:
            raise PermissionError('Video is locked')
        return delete_clip(path)
    library.delete_handler = remove
    monkeypatch.setattr(QMessageBox, 'question', lambda *args: QMessageBox.StandardButton.Yes)
    library.delete_selected()
    assert set(calls) == {locked, removable}
    assert not removable.exists() and not description_path(removable).exists()
    assert locked.exists() and read_description(locked) == 'preserved'
    assert library.selected_paths() == (locked,)
    assert 'Deleted 1 of 2' in library.message.text() and 'locked' in library.message.text()
    library.close()
    app.processEvents()


def test_library_saves_selection_changes_and_opens_editor_without_player(tmp_path):
    app = QApplication.instance() or QApplication([])
    for name in ('one.mp4', 'two.webm'):
        (tmp_path / name).write_bytes(b'video')
    (tmp_path / 'ignore.wav').write_bytes(b'audio')
    library = ClipLibrary(tmp_path)
    assert library.list.count() == 2
    selected = library.current
    library.description.setPlainText('remember this')
    library.list.setCurrentRow(1)
    assert read_description(selected) == 'remember this'
    opened = []
    library.open_clip.connect(opened.append)
    library.description.setPlainText('another clip')
    library.open_selected()
    assert opened == [library.current]
    assert read_description(library.current) == 'another clip'
    library.description.setPlainText('saved on escape')
    last = library.current
    library.reject()
    assert read_description(last) == 'saved on escape'
    app.processEvents()


def test_empty_library_and_deleted_clip(tmp_path):
    app = QApplication.instance() or QApplication([])
    library = ClipLibrary(tmp_path)
    assert not library.open_button.isEnabled()
    path = tmp_path / 'clip.mp4'
    path.touch()
    library.refresh()
    path.unlink()
    library.open_selected()
    assert 'no longer available' in library.message.text()
    library.close()
    app.processEvents()


def test_grid_preloads_thumbnails_and_keeps_them_while_browsing(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    from app.thumbnails import Thumbnails
    image = QImage(160, 90, QImage.Format.Format_RGB32)
    image.fill(QColor('green'))
    cached = tmp_path / 'preview.png'
    image.save(str(cached))
    monkeypatch.setattr(Thumbnails, 'request', lambda self, path: cached)
    for index in range(40):
        (tmp_path / f'clip-{index:02}.mp4').touch()
    library = ClipLibrary(tmp_path)
    library.show()
    app.processEvents()
    library._load_thumbnails()
    assert not library.list.item(0).icon().isNull()
    assert not library.list.item(39).icon().isNull()
    assert library.list.viewMode() == library.list.ViewMode.IconMode
    library.list.scrollToBottom()
    app.processEvents()
    library._load_thumbnails()
    assert not library.list.item(0).icon().isNull()
    assert not library.list.item(39).icon().isNull()
    library.close()
    app.processEvents()


def test_export_is_hidden_until_atomic_publish_and_then_appears(tmp_path):
    from app.exporter import ExportTarget
    app = QApplication.instance() or QApplication([])
    folder = tmp_path / 'clips'
    folder.mkdir()
    source = tmp_path / 'source.mp4'
    source.write_bytes(b'source')
    destination = folder / 'ready.mp4'
    target = ExportTarget(destination, source)
    try:
        target.temporary.write_bytes(b'partially written video')
        library = ClipLibrary(folder)
        assert library.list.count() == 0 and not library.open_button.isEnabled()
        target.temporary.write_bytes(b'finished video')
        library.refresh()
        assert library.list.count() == 0  # Complete bytes alone do not publish it.
        target.commit()
        library.refresh()
        assert library.list.count() == 1 and library.current == destination
        opened = []
        library.open_clip.connect(opened.append)
        library.open_selected()
        assert opened == [destination]
        library.close()
        app.processEvents()
    finally:
        target.cleanup()
