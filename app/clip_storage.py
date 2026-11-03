from __future__ import annotations

from datetime import datetime
import json
import os
import re
from pathlib import Path
from uuid import uuid4

from PySide6.QtCore import QStandardPaths


def clips_folder() -> Path:
    videos = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.MoviesLocation)
    if not videos:
        raise RuntimeError('Windows could not locate your Videos folder.')
    folder = Path(videos) / 'Seer'
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def clip_destination() -> Path:
    return clips_folder() / f'Clip-{datetime.now():%Y-%m-%d_%H-%M-%S}-{uuid4().hex[:8]}.mp4'


def description_path(path: Path) -> Path:
    return path.parent / 'metadata' / (path.name + '.seer.json')


def _metadata_file(path: Path, *, strict=False) -> Path:
    target = description_path(path)
    legacy = path.with_name(path.name + '.seer.json')
    try:
        if legacy.is_file():
            target.parent.mkdir(parents=True, exist_ok=True)
            if not target.exists():
                legacy.rename(target)
            elif legacy.read_bytes() == target.read_bytes():
                legacy.unlink()
            else:
                # Keep both versions if an older app wrote a second sidecar.
                legacy.rename(target.with_name(target.stem + '.legacy-' + uuid4().hex + '.json'))
    except OSError:
        if strict:
            raise
        if not target.exists():
            return legacy  # Existing descriptions remain readable if migration is blocked.
    return target


def read_description(path: Path) -> str:
    try:
        value = json.loads(_metadata_file(path).read_text(encoding='utf-8'))
        return str(value.get('description', '')) if isinstance(value, dict) else ''
    except (OSError, ValueError):
        return ''


def save_description(path: Path, text: str):
    if not path.is_file():
        raise FileNotFoundError('This clip is no longer available.')
    target = _metadata_file(path, strict=True)
    target.parent.mkdir(parents=True, exist_ok=True)
    stage = target.with_name(target.name + '.' + uuid4().hex + '.tmp')
    try:
        try:
            metadata = json.loads(target.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            metadata = {}
        if not isinstance(metadata, dict):
            metadata = {}
        metadata['description'] = text
        stage.write_text(json.dumps(metadata, ensure_ascii=False), encoding='utf-8')
        os.replace(stage, target)
    finally:
        stage.unlink(missing_ok=True)


def rename_clip(path: Path, filename: str) -> Path:
    if not filename or filename != filename.strip() or filename.endswith('.') or any(
            ord(c) < 32 or c in '<>:"/\\|?*' for c in filename):
        raise ValueError('Choose a valid Windows filename without folders or a trailing dot/space.')
    if filename.split('.')[0].upper() in {'CON', 'PRN', 'AUX', 'NUL',
            *(f'COM{i}' for i in range(1, 10)), *(f'LPT{i}' for i in range(1, 10))}:
        raise ValueError('That filename is reserved by Windows.')
    if Path(filename).suffix.lower() != path.suffix.lower():
        raise ValueError(f'Keep the {path.suffix} extension. Use Export to change video format.')
    target = path.with_name(filename)
    if path.name == filename:
        return path
    # Windows rename never replaces another clip. Refuse sidecar collisions too.
    if target.exists() and target.resolve() != path.resolve():
        raise FileExistsError('A clip with that filename already exists.')
    old_note, new_note = _metadata_file(path, strict=True), description_path(target)
    legacy_target = target.with_name(target.name + '.seer.json')
    if legacy_target.exists():
        raise FileExistsError('A description with that filename already exists.')
    if new_note.exists() and new_note.resolve() != old_note.resolve():
        raise FileExistsError('A description with that filename already exists.')
    path.rename(target)
    try:
        if old_note.exists():
            old_note.rename(new_note)
    except OSError:
        target.rename(path)
        raise
    return target


def delete_clip(path: Path) -> list[str]:
    """Delete the video first; a locked video must leave its metadata intact."""
    path.unlink(missing_ok=True)
    note = description_path(path)
    associated = [note, path.with_name(path.name + '.seer.json')]
    warnings = []
    try:
        if note.parent.is_dir():
            pattern = re.compile(re.escape(note.stem) + r'\.legacy-[0-9a-f]{32}\.json')
            associated += [p for p in note.parent.iterdir() if pattern.fullmatch(p.name)]
    except OSError as error:
        warnings.append(str(error))
    for metadata in associated:
        try:
            metadata.unlink(missing_ok=True)
        except OSError as error:
            warnings.append(f'{metadata.name}: {error}')
    return warnings


def migrate_metadata(folder: Path):
    for legacy in folder.glob('*.seer.json'):
        if legacy.is_file() and len(legacy.name) > len('.seer.json'):
            _metadata_file(legacy.with_name(legacy.name[:-len('.seer.json')]))
