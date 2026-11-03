# Memory optimization results

Measured on this Windows 11 machine using Python 3.12.16 / PySide6 6.12.0, a generated 1920×1080 H.264/AAC video, native Windows rendering, and the same open/play/close/reopen sequence. These are short local measurements, not universal limits or a Medal comparison.

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
