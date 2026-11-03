# Memory optimization results

Final `dist/Seer` build: 51.89 MiB startup working set / 25.94 MiB private commit; 56.43 MiB working set / 27.59 MiB private commit after each of two editor open/close cycles. The packaged diagnostic reported no errors and no Qt Multimedia import into the tray process.

The current `dist/sources/Seer` packaged diagnostic measured 51.91 MiB working set / 25.89 MiB private commit at startup and 56.45 / 56.46 MiB working set after two editor open/close cycles (27.58 MiB private commit). No Qt Multimedia module was imported into the tray process, and the check reported no errors. These figures exclude active capture and an open manager. A separate native library check verified that the shared GUI process exits when both its manager and editor windows close.

Capture update (November 3, 2026): short four-second 1920x1080/30 FPS AMD AMF capture-process checks, using one conversion filter thread, measured GDI at 104.41 MiB peak sampled working set / 121.73 MiB private committed memory, and Desktop Duplication at 105.28 / 162.32 MiB. These figures cover FFmpeg alone, exclude GPU memory, and are not total application or long-run measurements. Desktop Duplication avoids the GDI capture path implicated in cursor flicker; its committed-memory cost remains higher on this hardware. Selecting All with ordinary system/microphone audio still uses this capture path and starts no native helper. Selected windows and selected-app audio now use optional native helper processes and can require additional RAM and GPU memory; their long-run resource use has not been profiled.

The earlier `dist/updated/Seer` editor diagnostic measured 51.29 MiB working set at idle startup and 56.00 / 56.01 MiB after two editor open/close cycles, with no reported errors and no Qt Multimedia import in the tray process. These historical idle readings exclude active capture and an open library. The new clip manager preloads its thumbnail grid and keeps an editor ready inside a separate GUI process. It can use more memory while open; closing both manager and editor exits the GUI process, releasing its decoder, thumbnails, and allocations. Native checks verified process exit and roughly 0.44-0.50 second warm-editor metadata loading on a short local test clip, rather than a universal latency guarantee.

Measured on this Windows 11 machine using Python 3.12.16 / PySide6 6.12.0, a generated 1920x1080 H.264/AAC video, native Windows rendering, and the same open/play/close/reopen sequence. These are short local measurements, not universal limits or a Medal comparison.

| Phase | Original working set | Optimized working set |
| --- | ---: | ---: |
| Fresh tray startup | 68.93 MiB | 57.02 MiB |
| Playback, all Seer processes | 244.93 MiB | 298.12 MiB |
| Back in tray after closing editor | 244.64 MiB | 58.31 MiB |
| Back in tray after reopening/closing again | 244.72 MiB | 58.32 MiB |

Post-editor working set fell about **76%**. Private committed memory after the first close fell from 315.45 MiB to 30.09 MiB. Working set means resident memory; private commit is a different measurement and should not be added to it.

The original implementation created the editor/player at startup and retained them after closing. Lazy creation and explicit destruction helped, but left native codec/driver allocations resident across reopen cycles. A separate editor process releases those allocations when it exits. The tray never imports Qt Multimedia and retains only plain editing metadata. The editor is recreated with the saved crop, trim, position, and export options when requested.

**Trade-off:** total memory while editing is higher because the small tray process also remains alive. Summed working sets count shared DLL pages in both processes. Combined private commit during the first preview was 330.85 MiB versus 315.70 MiB originally. This change targets the utility's long-lived background footprint; it does not claim lower active-decoding or recording memory.

No EmptyWorkingSet calls, heap trimming, or forced paging were used to make Task Manager numbers look lower. Actual editor processes exit, and process handles/pipes/timers are released. Long loopback silence is also written in bounded chunks, avoiding a single allocation proportional to the silence duration.

The final packaged executable was checked separately using its real Windows spawn path: 51.16 MiB working set at startup, 55.86 MiB after the first editor close, and 55.88 MiB after reopening/closing. Private commit was 26.56 MiB after each close. Both editor processes exited cleanly and Qt Multimedia remained absent from the tray process. Profile JSON files and generated clips live in `.tools` and are excluded from source control.
