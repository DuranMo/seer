from __future__ import annotations

import sys
from multiprocessing import freeze_support

if __name__ == '__main__':
    freeze_support()

from PySide6.QtCore import QDir, QLockFile, QStandardPaths
from PySide6.QtWidgets import QApplication, QMessageBox, QSystemTrayIcon

from app.main_window import MainWindow
from app.icons import application_icon
from app.styles import apply_colors


def main() -> int:
    if len(sys.argv) == 4 and sys.argv[1] == '--check-editor':
        from pathlib import Path
        from app.editor_check import run_check
        return run_check(Path(sys.argv[2]), Path(sys.argv[3]))
    app = QApplication(sys.argv)
    app.setApplicationName('Seer')
    app.setOrganizationName('Seer')
    apply_colors(app)
    app.setWindowIcon(application_icon())
    app.setQuitOnLastWindowClosed(False)
    if not QSystemTrayIcon.isSystemTrayAvailable():
        QMessageBox.critical(None, 'Seer', 'The Windows system tray is unavailable. Seer needs a tray to run in the background.')
        return 1
    location = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppLocalDataLocation)
    QDir().mkpath(location)
    lock = QLockFile(location + '/seer.lock')
    if not lock.tryLock(100):
        QMessageBox.information(None, 'Seer is already running', 'Right-click the Seer icon beside the Windows clock.')
        return 0
    window = MainWindow()
    return app.exec()


if __name__ == '__main__':
    raise SystemExit(main())
