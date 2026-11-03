from __future__ import annotations

from pathlib import Path

from app.sessions import RecordingSessions


def capture_files(folder: Path):
    for name in ('screen.mp4', 'audio.wav', 'recording.mp4'):
        (folder / name).write_bytes(b'temporary capture')


def test_cleanup_removes_only_owned_capture_files(tmp_path, monkeypatch):
    folder = tmp_path / 'session'
    folder.mkdir()
    monkeypatch.setattr('app.sessions.tempfile.mkdtemp', lambda **kwargs: str(folder))
    sessions = RecordingSessions()
    assert sessions.create() == folder.resolve()
    capture_files(folder)
    imported = tmp_path / 'imported.mp4'
    exported = tmp_path / 'saved.mp4'
    imported.write_bytes(b'user original')
    exported.write_bytes(b'finished export')
    sessions.protect_export(exported)
    assert sessions.cleanup() == []
    assert not folder.exists()
    assert imported.read_bytes() == b'user original'
    assert exported.read_bytes() == b'finished export'


def test_even_export_inside_session_with_intermediate_name_is_kept(tmp_path, monkeypatch):
    folder = tmp_path / 'session'
    folder.mkdir()
    monkeypatch.setattr('app.sessions.tempfile.mkdtemp', lambda **kwargs: str(folder))
    sessions = RecordingSessions()
    sessions.create()
    capture_files(folder)
    saved = folder / 'recording.mp4'
    saved.write_bytes(b'exported video')
    sessions.protect_export(saved)
    other = folder / 'notes.txt'
    other.write_text('keep this', encoding='utf-8')
    assert sessions.cleanup() == []
    assert not (folder / 'screen.mp4').exists()
    assert not (folder / 'audio.wav').exists()
    assert saved.read_bytes() == b'exported video'
    assert other.exists()


def test_cleanup_retries_a_transient_windows_lock(tmp_path, monkeypatch):
    folder = tmp_path / 'session'
    folder.mkdir()
    monkeypatch.setattr('app.sessions.tempfile.mkdtemp', lambda **kwargs: str(folder))
    sessions = RecordingSessions()
    sessions.create()
    capture_files(folder)
    original = Path.unlink
    attempts = []

    def locked_once(path, **kwargs):
        if path.name == 'screen.mp4':
            attempts.append(path)
            if len(attempts) == 1:
                raise PermissionError('Decoder is releasing the file')
        return original(path, **kwargs)

    monkeypatch.setattr(Path, 'unlink', locked_once)
    assert sessions.cleanup() == []
    assert len(attempts) == 2
    assert not folder.exists()
