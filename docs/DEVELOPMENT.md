# Development

Windows 10/11, Python 3.11 or newer.

## Run

In PowerShell from the project folder:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements-dev.txt
.\.venv\Scripts\python main.py
```

Install a full Windows [FFmpeg build](https://ffmpeg.org/download.html), version 7 or newer. Put `ffmpeg.exe` and `ffprobe.exe` in `bin` before running Seer, or add them to PATH. You can also set `SEER_FFMPEG` and `SEER_FFPROBE` to their full paths.

Audio capture uses PyAudioWPatch's WASAPI backend. FFmpeg does not need a WASAPI input. One system-audio or microphone device can be recorded at a time.

## Check

```powershell
.\.venv\Scripts\python -m pytest
```

Integration tests need FFmpeg and FFprobe. Optional checks below actually record the desktop and system audio:

```powershell
.\.venv\Scripts\python tools\smoke_replay.py --audio system
.\.venv\Scripts\python tools\smoke_replay_ui.py
```

See [validation results](VALIDATION.md) and [memory measurements](MEMORY.md) for tested behavior and remaining limitations.

## Build

```powershell
.\.venv\Scripts\pyinstaller --noconfirm seer.spec
```

Quit any copy running from the output folder first. Distribute the **whole** `dist\Seer` folder, including `_internal`; `Seer.exe` alone will not work. Python is not required on the recipient's computer. FFmpeg and FFprobe are bundled when present in `bin` during the build.

## Publish

Commit source, tests, tools, and documentation. `.gitignore` excludes downloaded tools, environments, builds, and recordings. Upload a ZIP of the complete built folder as a GitHub Release asset named `Seer-Windows.zip`.

Choose a license for Seer's source before publishing. Before distributing binaries, include the required dependency licenses and notices and satisfy the source-code requirements for the particular FFmpeg and Qt builds you bundle. See [FFmpeg licensing](https://ffmpeg.org/legal.html) and [Qt for Python licensing](https://doc.qt.io/qtforpython-6/licenses.html).
