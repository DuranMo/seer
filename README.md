## Preface

This project served two goals:
1) to help create a low-cost screen recording app with a built in editing system
2) to explore AI-assisted software development (particularly using Codex).

# Seer

A small screen recorder & replay clipper for Windows

## Install

1. download `Seer-Windows.zip` from [releases](https://github.com/DuranMo/seer/releases)
2. extract the whole folder
3. open `Seer.exe`

Seer runs in the system tray beside the clock. Check the hidden icons arrow if you don't see it.

## Use

Press **Ctrl+Shift+R** to start recording & press it again to stop. Crop the video, set the start/end times, and click **Export** to save it.

For clips, right-click the tray icon --> **Replay --> Enabled**. Choose a length up to 2 minutes, then press **Ctrl+Shift+F8** to save what just happened. Clips automatically go to **Videos\Seer**. You can record while replay is enabled.

Right-click the tray icon --> **Clips** to browse saved videos, rename or delete clips, edit their descriptions, or open a clip in the editor. Deleting asks for confirmation and permanently removes the video and its metadata. Titles are the actual filenames; descriptions are separate. **Keybinds** lets you change both shortcuts in one place.

In the clip grid, **Ctrl-click** or **Shift-click** to select multiple clips, or **Ctrl+A** to select all. Click **Delete** to remove the selection with one confirmation.

Use **Recording --> Sources** to choose the whole primary display or selected windows, and system, microphone, or selected app audio. Selected windows share one tiled video; selected apps share one mixed audio track.

Recording frame rates go up to 240 FPS, with **Same as source** to match the display refresh rate. Right-click **Export** in the editor for other output formats and settings.

Closing the editor returns to the tray. **Quit** deletes temporary recordings, so export anything you want to keep first. Automatically saved clips stay.

[building from source](docs/DEVELOPMENT.md)
