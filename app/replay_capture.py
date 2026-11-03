"""Safe FFmpeg capture arguments and encoder capability checks for replay."""
from __future__ import annotations

from pathlib import Path
import subprocess

from .exporter import RATES
from .video_utils import executable, run_checked


def encoder_args(encoder: str, fps: int) -> list[str]:
    common = ['-c:v', encoder, '-g', str(fps), '-bf', '0', '-b:v', '8M', '-maxrate', '12M', '-bufsize', '24M']
    if encoder == 'h264_nvenc':
        return common + ['-preset', 'p3', '-tune', 'll', '-rc', 'vbr', '-forced-idr', '1']
    if encoder == 'h264_qsv':
        return common + ['-preset', 'veryfast']
    if encoder == 'h264_amf':
        return common + ['-quality', 'speed', '-usage', 'lowlatency', '-rc', 'vbr_peak', '-forced_idr', '1', '-async_depth', '2']
    if encoder == 'libx264':
        return common + ['-preset', 'ultrafast', '-tune', 'zerolatency', '-crf', '22',
                         '-keyint_min', str(fps), '-sc_threshold', '0', '-threads', '2']
    raise ValueError('Unsupported replay encoder.')


def choose_encoder(width: int, height: int, fps: int) -> str:
    tool = executable('ffmpeg')
    available = run_checked([tool, '-hide_banner', '-encoders'])
    for encoder in ('h264_nvenc', 'h264_qsv', 'h264_amf', 'libx264'):
        if encoder not in available:
            continue
        try:
            run_checked([tool, '-hide_banner', '-v', 'error', '-f', 'lavfi', '-i',
                f'color=size={width + width % 2}x{height + height % 2}:rate={fps}:duration=0.1',
                '-an'] + encoder_args(encoder, fps) + ['-frames:v', '2', '-f', 'null', '-'], timeout=8)
            return encoder
        except (RuntimeError, subprocess.TimeoutExpired):
            continue
    raise RuntimeError('No supported replay H.264 encoder could be opened.')


def replay_args(folder: Path, fps: int, geometry: tuple[int, int, int, int], encoder: str) -> list[str]:
    if fps not in RATES:
        raise ValueError('Unsupported recording frame rate.')
    x, y, width, height = geometry
    return ['-hide_banner', '-y', '-copyts', '-f', 'gdigrab', '-framerate', str(fps),
        '-draw_mouse', '1', '-offset_x', str(x), '-offset_y', str(y), '-video_size',
        f'{width}x{height}', '-i', 'desktop', '-an', '-vf',
        'showinfo,setpts=PTS-STARTPTS,pad=ceil(iw/2)*2:ceil(ih/2)*2',
        '-pix_fmt', 'yuv420p'] + encoder_args(encoder, fps) + [
        '-force_key_frames', 'expr:gte(t,n_forced)', '-fps_mode', 'vfr',
        '-f', 'segment', '-segment_time', '1', '-segment_format', 'mpegts',
        '-segment_format_options', 'mpegts_copyts=1',
        '-segment_list', str(folder / 'segments.csv'), '-segment_list_type', 'csv',
        '-segment_list_size', '8', '-segment_list_flags', '+live',
        '-reset_timestamps', '0', str(folder / 'v-%010d.ts')]
