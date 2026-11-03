"""Own and remove capture intermediates without touching imports or saved exports."""
from __future__ import annotations

from pathlib import Path
import tempfile
import time


class RecordingSessions:
    def __init__(self):
        self.folders: list[Path] = []
        self.exports: set[Path] = set()

    def create(self) -> Path:
        folder = Path(tempfile.mkdtemp(prefix='seer-session-')).resolve()
        self.folders.append(folder)
        return folder

    def protect_export(self, path: Path):
        self.exports.add(path.resolve())

    def cleanup(self) -> list[Path]:
        failed = []
        for folder in self.folders:
            # Delete only files Seer creates, never recursively remove a directory.
            # An export saved inside the session folder is explicitly protected.
            for name in ('screen.mp4', 'audio.wav', 'recording.mp4'):
                path = folder / name
                resolved = path.resolve()
                if resolved in self.exports:
                    continue
                if resolved.parent != folder:
                    failed.append(path)
                    continue
                for attempt in range(4):
                    try:
                        path.unlink(missing_ok=True)
                        break
                    except OSError:
                        if attempt == 3:
                            failed.append(path)
                        else:
                            time.sleep(0.1)
            try:
                folder.rmdir()  # Leave nonempty folders containing exports/other files.
            except OSError:
                pass
        return failed
