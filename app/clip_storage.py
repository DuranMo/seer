from __future__ import annotations

from datetime import datetime
from pathlib import Path
from uuid import uuid4

from PySide6.QtCore import QStandardPaths


def clip_destination() -> Path:
    videos = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.MoviesLocation)
    if not videos:
        raise RuntimeError('Windows could not locate your Videos folder.')
    folder = Path(videos) / 'Seer'
    folder.mkdir(parents=True, exist_ok=True)
    return folder / f'Clip-{datetime.now():%Y-%m-%d_%H-%M-%S}-{uuid4().hex[:8]}.mp4'
