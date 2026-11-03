from __future__ import annotations


def clock(seconds: float) -> str:
    minutes, seconds = divmod(max(0, seconds), 60)
    hours, minutes = divmod(int(minutes), 60)
    return f'{hours:02}:{minutes:02}:{seconds:06.3f}'


def seconds_label(seconds: float) -> str:
    return f'{max(0, seconds):.3f}'.rstrip('0').rstrip('.') + 's'
