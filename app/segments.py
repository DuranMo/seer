"""Timestamp-based segment retention shared by video and rolling audio."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import threading


@dataclass(frozen=True)
class Segment:
    path: Path
    start: float
    end: float


class SegmentRing:
    def __init__(self, folder: Path, retention: float = 125):
        self.folder = folder.resolve()
        self.retention = retention
        self.segments: dict[Path, Segment] = {}
        self.leases: dict[int, tuple[float, float]] = {}
        self.lock = threading.RLock()
        self.next_lease = 0

    def add(self, segment: Segment):
        if segment.path.resolve().parent != self.folder or segment.end <= segment.start:
            raise ValueError('Invalid capture segment.')
        with self.lock:
            self.segments[segment.path] = segment

    def reserve(self, start: float, end: float = float('inf')) -> int:
        with self.lock:
            self.next_lease += 1
            self.leases[self.next_lease] = (start - 2, end + 2)
            return self.next_lease

    def release(self, lease: int):
        with self.lock:
            self.leases.pop(lease, None)

    def select(self, start: float, end: float) -> list[Segment]:
        with self.lock:
            return sorted((s for s in self.segments.values() if s.end > start and s.start < end), key=lambda s: s.start)

    def bounds(self) -> tuple[float, float] | None:
        with self.lock:
            if not self.segments:
                return None
            return min(s.start for s in self.segments.values()), max(s.end for s in self.segments.values())

    def prune(self, now: float):
        with self.lock:
            for path, segment in list(self.segments.items()):
                if segment.end >= now - self.retention:
                    continue
                if any(segment.end > start and segment.start < end for start, end in self.leases.values()):
                    continue
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    continue  # Retry next tick if Windows temporarily locks a file.
                del self.segments[path]


def concat_manifest(segments: list[Segment]) -> str:
    lines = ['ffconcat version 1.0']
    for segment in segments:
        path = segment.path.resolve().as_posix().replace("'", "'\\''")
        lines += [f"file '{path}'", f'duration {segment.end - segment.start:.9f}']
    return '\n'.join(lines) + '\n'
