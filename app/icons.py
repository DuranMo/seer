from __future__ import annotations

from pathlib import Path
import sys

from PySide6.QtGui import QIcon


def application_icon() -> QIcon:
    root = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent.parent))
    icon = QIcon(str(root / 'seer_logo.png'))
    if icon.isNull():
        raise RuntimeError('The application logo seer_logo.png is missing or unreadable.')
    return icon
