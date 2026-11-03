"""Confirm deletion of a temporary copy through the real manager and decoder."""
from pathlib import Path
import shutil
import sys
import tempfile
import argparse

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QMessageBox
from app.clip_library import ClipLibrary
from app.clip_storage import description_path, save_description
from app.editor_window import Editor
from app.styles import apply_colors


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('--bulk', action='store_true')
    options = parser.parse_args()
    Path('.tools').mkdir(exist_ok=True)
    folder = Path(tempfile.mkdtemp(prefix='delete-check-', dir='.tools')).resolve()
    clips = [folder / f'temporary-test-copy-{index}.mp4' for index in range(3 if options.bulk else 1)]
    for clip in clips:
        shutil.copyfile(options.source, clip)
        save_description(clip, 'Test description')
    app = QApplication([])
    app.setQuitOnLastWindowClosed(False)
    apply_colors(app)
    editor = Editor()
    library = ClipLibrary(folder)
    library.thumbnails.cache = folder / 'cache'
    library.delete_handler = editor.delete_source
    errors = []
    armed = False

    def confirm():
        box = app.activeModalWidget()
        assert isinstance(box, QMessageBox)
        assert box.defaultButton() == box.button(QMessageBox.StandardButton.No)
        box.button(QMessageBox.StandardButton.Yes).click()

    def check():
        try:
            assert editor.media is not None
            if options.bulk:
                library.list.selectAll()
            QTimer.singleShot(0, confirm)
            library.delete_selected()
            assert all(not clip.exists() and not description_path(clip).exists() for clip in clips)
            assert editor.media is None and library.current is None
            assert library.list.count() == 0 and not library.delete_button.isEnabled()
            print(f'OK: one confirmation defaults to No; deleted {len(clips)} clips, released the active video, removed metadata, and refreshed the manager.', flush=True)
        except Exception as error:
            errors.append(str(error))
            print('FAILED:', error, flush=True)
        finally:
            library.close()
            editor.release_playback()
            editor.close()
            app.exit(int(bool(errors)))

    def loaded():
        nonlocal armed
        if not armed:
            armed = True
            QTimer.singleShot(300, check)

    library.show()
    editor.show()
    editor.idle.connect(loaded)
    editor.load(clips[0])
    QTimer.singleShot(15000, lambda: (errors.append('Timed out'), app.exit(1)))
    return app.exec()


if __name__ == '__main__':
    raise SystemExit(main())
