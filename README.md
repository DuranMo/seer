## Preface

This project served two goals:
1) to help create a low-cost screen recording app with a built in editing system
2) to explore AI-assisted software development (particularly using Codex).

# Seer

A small screen recorder & replay clipper for windows

## Install

1. download `Seer-Windows.zip` from [releases](https://github.com/DuranMo/seer/releases)
2. extract the whole folder
3. open `Seer.exe`

Seer runs in the system tray beside the clock. Check the hidden icons arrow if you don't see it.

## Use

Press **Ctrl+Shift+R** to start recording & press it again to stop. Crop the video, set the start/end times, and click **Export** to save it.

For clips, right-click the tray icon --> **Replay --> Enabled**. Choose a length up to 2 minutes, then press **Ctrl+Shift+F8** to save what just happened. Clips automatically go to **Videos\Seer**. You can record while replay is enabled.

Right-click the tray icon to change keybinds, recording frame rate, or audio. Right-click **Export** in the editor for other output formats and settings.

Closing the editor returns to the tray. **Quit** deletes temporary recordings, so export anything you want to keep first. Automatically saved clips stay.

[building from source](docs/DEVELOPMENT.md)
