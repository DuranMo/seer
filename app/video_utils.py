from __future__ import annotations

import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
from dataclasses import dataclass
from fractions import Fraction


def executable(name: str) -> str:
    """Explicit env override, packaged bin, project bin, then PATH."""
    override = os.environ.get('SEER_' + name.upper())
    root = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent.parent))
    suffix = '.exe' if sys.platform == 'win32' else ''
    candidates = [override, str(root / 'bin' / (name + suffix)), shutil.which(name)]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return str(Path(candidate).resolve())
    raise RuntimeError(f'{name} is missing. Add it to PATH or set SEER_{name.upper()} to its executable path.')


def run_checked(args: list[str], timeout: float = 60) -> str:
    result = subprocess.run(args, capture_output=True, text=True, encoding='utf-8',
                            errors='replace', timeout=timeout,
                            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0)
    if result.returncode:
        raise RuntimeError(result.stderr[-5000:] or f'{Path(args[0]).name} failed.')
    return result.stdout


def number(value, default: float = 0.0) -> float:
    try:
        result = float(Fraction(str(value)))
        return result if math.isfinite(result) else default
    except (ValueError, ZeroDivisionError, TypeError):
        return default


@dataclass(frozen=True)
class Media:
    path: Path
    width: int
    height: int
    duration: float
    has_audio: bool
    average_rate: str
    nominal_rate: str
    time_base: str
    start_time: float
    timestamps: tuple[float, ...]
    variable_rate: bool
    video_index: int = 0


def probe(path: Path) -> Media:
    path = path.resolve(strict=True)
    tool = executable('ffprobe')
    data = json.loads(run_checked([tool, '-v', 'error', '-show_streams', '-show_format',
                                   '-of', 'json', str(path)]))
    video = next((s for s in data['streams'] if s['codec_type'] == 'video'
                  and not s.get('disposition', {}).get('attached_pic')), None)
    if video is None:
        raise ValueError('Choose a file containing a video track.')
    rotation = next((int(s.get('rotation', 0)) for s in video.get('side_data_list', [])
                     if 'rotation' in s), int(video.get('tags', {}).get('rotate', 0)))
    width, height = int(video['width']), int(video['height'])
    if abs(rotation) % 180 == 90:
        width, height = height, width
    sar = number(video.get('sample_aspect_ratio', '1:1').replace(':', '/'), 1)
    if sar > 0 and sar != 1:
        raise ValueError('This video uses non-square pixels. Convert it to square pixels before importing.')
    # Bound frame inspection, even for long recordings. Timestamps are diagnostic;
    # neither trim nor seek ever converts time into frame indices.
    frames = json.loads(run_checked([tool, '-v', 'error', '-select_streams', str(video['index']),
        '-read_intervals', '%+30', '-show_frames', '-show_entries',
        'frame=best_effort_timestamp_time,pkt_duration_time', '-of', 'json', str(path)]))
    stamps = tuple(number(f['best_effort_timestamp_time']) for f in frames.get('frames', [])
                   if 'best_effort_timestamp_time' in f)
    deltas = [b - a for a, b in zip(stamps, stamps[1:]) if b > a]
    vfr = bool(deltas and max(deltas) - min(deltas) > max(0.0001, min(deltas) * 0.02))
    duration = number(video.get('duration')) or number(data.get('format', {}).get('duration'))
    if duration <= 0:
        raise ValueError('The video has no usable duration.')
    return Media(path, width, height, duration,
        any(s['codec_type'] == 'audio' for s in data['streams']),
        video.get('avg_frame_rate', '0/0'), video.get('r_frame_rate', '0/0'),
        video.get('time_base', ''), number(data.get('format', {}).get('start_time')),
        stamps, vfr, int(video['index']))


@dataclass(frozen=True)
class Crop:
    x: int
    y: int
    width: int
    height: int

    def validate(self, width: int, height: int) -> None:
        if min(self.x, self.y) < 0 or min(self.width, self.height) < 2:
            raise ValueError('Crop must be at least 2 × 2 pixels and inside the video.')
        if self.x + self.width > width or self.y + self.height > height:
            raise ValueError('Crop extends beyond the video.')


def preview_crop(rect: tuple[float, float, float, float],
                 viewport: tuple[float, float, float, float], width: int, height: int) -> Crop:
    """Map a letterboxed preview rectangle into source pixel coordinates."""
    x, y, w, h = rect
    vx, vy, vw, vh = viewport
    if vw <= 0 or vh <= 0:
        raise ValueError('Preview has no size.')
    left = max(0, min(width - 2, round((x - vx) * width / vw)))
    top = max(0, min(height - 2, round((y - vy) * height / vh)))
    right = max(left + 2, min(width, round((x + w - vx) * width / vw)))
    bottom = max(top + 2, min(height, round((y + h - vy) * height / vh)))
    return Crop(left, top, right - left, bottom - top)


def validate_trim(start: float, end: float, duration: float) -> None:
    if not all(math.isfinite(t) for t in (start, end, duration)):
        raise ValueError('Trim timestamps must be finite.')
    if start < 0 or end <= start or end > duration + 0.001:
        raise ValueError('Choose a start before the end, within the video duration.')
