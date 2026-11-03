# Build: pyinstaller --noconfirm seer.spec
from pathlib import Path
from PySide6.QtCore import Qt
from PySide6.QtGui import QImage

root = Path(SPECPATH)
logo = root / 'seer_logo.png'
icon_path = root / 'build' / 'seer.ico'
icon_path.parent.mkdir(parents=True, exist_ok=True)
image = QImage(str(logo)).scaled(256, 256, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
if image.isNull() or not image.save(str(icon_path)):
    raise RuntimeError('Could not convert seer_logo.png to a Windows application icon.')
tools = [(str(root / 'bin' / name), 'bin') for name in ('ffmpeg.exe', 'ffprobe.exe', 'seer-capture.exe')
         if (root / 'bin' / name).is_file()]
a = Analysis(['main.py'], pathex=[str(root)], binaries=tools, datas=[(str(logo), '.')],
             hiddenimports=['pyaudiowpatch'],
             excludes=['PySide6.QtWebEngineCore', 'PySide6.QtWebEngineWidgets', 'PySide6.QtPdf'])
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='Seer', console=False, icon=str(icon_path))
coll = COLLECT(exe, a.binaries, a.datas, name='Seer')
