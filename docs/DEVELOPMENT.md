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

System/microphone capture uses PyAudioWPatch's WASAPI backend. FFmpeg does not need a WASAPI input. Selected-app audio uses the optional native process-loopback helper; selected process trees are mixed into one stereo track. Selecting a parent and its child does not duplicate their audio. Source counts have no fixed application cap; resource use scales with the selection.

Capture prefers FFmpeg's Desktop Duplication (`ddagrab`) on the primary display to avoid GDI cursor flicker. It probes the actual output and falls back to `gdigrab` if unavailable. Both use wall-clock packet timestamps for audio alignment. A fullscreen mode/display change may require restarting replay. Capture conversion uses a single filter thread to bound memory use.

Selected-window capture requests Windows borderless-capture access, then disables the session border through the supported API. Windows retains the border if permission is denied or the setting is unsupported. Gallery scans exclude `.seer-` staging files: an app-produced clip becomes visible only after encoding, validation, and atomic publication under its final filename.

Audio maps the capture clock to wall time and preserves continuous PCM through small timestamp jitter. Real silence gaps retain their duration. The clip manager and editor share a separate GUI process. The manager preloads its thumbnail grid and keeps the editor warm while open; closing both windows exits that process. Descriptions remain in `.seer.json` sidecars. Opening a video reads stream metadata first and inspects two seconds of presentation timestamps asynchronously, without converting trimming/playback positions to frame numbers. General `probe()` callers still inspect up to 30 seconds by default.

The clip shortcut shows an optimistic **Clip saved** notification immediately after accepting the request. Assembly and exact trimming continue in the background; failures produce an error notification. The file appears in the manager when finalization succeeds. The manager's title field is the actual filename: saving it renames the video and description sidecar together, refuses collisions, and updates an active editor's source. Description text is stored independently in `Videos\Seer\metadata\<filename>.seer.json` and never supplies the title. Opening the manager migrates older sidecars out of the video folder; conflicting versions are preserved in metadata backups.

The manager uses native extended selection: Ctrl-click, Shift-click, and Ctrl+A. Details are editable only for a single selection. **Delete** asks once for the entire selection, with **No** selected by default. Confirming releases an active editor source, removes each selected video and matching metadata/cache files, and updates library/tray references. A locked clip does not prevent deleting the others; failures remain selected for retry. File operations live in `app/clip_storage.py`, separate from gallery widgets. Deletion is blocked while opening/exporting.

Fixed capture/export presets include 15, 24, 30, 60, 90, 120, 144, 165, and 240 FPS. Recording's **Same as source (display refresh)** resolves the source monitor rate at capture start, using the fastest selected monitor for a tiled stream. Windows display paths provide rational rates for selected windows, so friendly monitor names do not need to match Qt screen names. This matches configured display refresh, not a game's internal rendering FPS or a continuously changing VRR rate. Export's separate **Same as source** continues to preserve timestamp-based source timing. Higher rates need sufficient encoder/capture throughput.

## Optional Windows sources

Build the small helper using a current MinGW-w64 UCRT64 compiler and headers:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File tools\build_native.ps1
```

This writes `bin\seer-capture.exe`, which PyInstaller bundles automatically. No extra Python package is required. Window capture uses Windows Graphics Capture and requires Windows build 19041 or newer. App audio uses process-loopback capture and requires build 20348 or newer. **All (primary display)** with ordinary system/microphone audio keeps the existing backend and starts no native helper.

Choose sources in the tray's **Recording → Sources** dialog before enabling replay. Selected windows are tiled into one video; one window uses its own initial dimensions. Lists remain enabled for scrolling when All/Primary is selected; checkboxes become editable in Selected mode. Source settings are locked during capture. Windows are checked against their owning process identity before starting. Invalid or unavailable selections fail instead of falling back to the desktop. Restore minimized windows before starting; minimizing during capture blanks that tile. Resizing preserves the fixed output canvas. Closing a selected window stops capture with an error. Protected content and individual games can restrict window capture.

Optional native checks open temporary test windows or play test audio:

```powershell
.\.venv\Scripts\python tools\smoke_sources.py --replay --app-audio
.\.venv\Scripts\python tools\check_app_audio.py
.\.venv\Scripts\python tools\smoke_library.py C:\Videos\sample.mp4
.\.venv\Scripts\python tools\smoke_delete.py C:\Videos\sample.mp4 --bulk
```

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
