"""Render the small editor; use QT_QPA_PLATFORM=windows for native typography."""
from __future__ import annotations

import os
from pathlib import Path
import sys

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import QRectF, QTimer
from PySide6.QtWidgets import QApplication

from app.editor_window import Editor
from app.icons import application_icon
from app.styles import apply_colors


app = QApplication([])
apply_colors(app)
app.setWindowIcon(application_icon())
editor = Editor()
editor.setWindowTitle('Seer')
editor.resize(600, 440)
editor.show()


def seek():
    editor.player.setPosition(500)
    QTimer.singleShot(500, finish)


def finish():
    editor.grab().save('.tools/editor-simple.png')
    editor.preview.crop = QRectF(0.2, 0.15, 0.55, 0.7)
    editor.preview.update()
    QTimer.singleShot(100, cropped)


def cropped():
    editor.grab().save('.tools/editor-cropped.png')
    editor.player.stop()
    editor.close()
    app.quit()


if len(sys.argv) > 1:
    editor.load(Path(sys.argv[1]))
QTimer.singleShot(2000, seek)
raise SystemExit(app.exec())
