"""Color preferences only. Widgets keep the platform's native style and metrics."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication


def apply_colors(app: QApplication) -> None:
    app.styleHints().setColorScheme(Qt.ColorScheme.Dark)
    palette = app.palette()
    colors = {
        QPalette.ColorRole.Window: '#000000',
        QPalette.ColorRole.Base: '#000000',
        QPalette.ColorRole.AlternateBase: '#111111',
        QPalette.ColorRole.Button: '#202020',
        QPalette.ColorRole.ToolTipBase: '#202020',
        QPalette.ColorRole.WindowText: '#EEEEEE',
        QPalette.ColorRole.Text: '#EEEEEE',
        QPalette.ColorRole.ButtonText: '#EEEEEE',
        QPalette.ColorRole.ToolTipText: '#EEEEEE',
        QPalette.ColorRole.Highlight: '#557E5C',
        QPalette.ColorRole.HighlightedText: '#FFFFFF',
        QPalette.ColorRole.Accent: '#557E5C',
    }
    for role, color in colors.items():
        palette.setColor(role, QColor(color))
    for role in (QPalette.ColorRole.WindowText, QPalette.ColorRole.Text, QPalette.ColorRole.ButtonText):
        palette.setColor(QPalette.ColorGroup.Disabled, role, QColor('#777777'))
    app.setPalette(palette)
