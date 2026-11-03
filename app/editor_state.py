"""Small, Qt-free editing state retained while the editor is closed."""
from __future__ import annotations

from dataclasses import dataclass

from .video_utils import Media


@dataclass(frozen=True)
class EditorState:
    media: Media
    temporary_source: bool
    start: float
    end: float
    position: int
    crop: tuple[float, float, float, float]
    format: str
    fps: int | None
    include_audio: bool
