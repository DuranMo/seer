from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import tempfile

from .video_utils import Crop, Media, validate_trim

RATES = (15, 24, 30, 60, 90, 120, 144, 165, 240)
VIDEO_FORMATS = ('mp4', 'webm', 'gif', 'mov')
AUDIO_FORMATS = ('mp3', 'wav', 'flac', 'm4a')
STAGING_PREFIX = '.seer-'


@dataclass(frozen=True)
class ExportOptions:
    format: str
    start: float
    end: float
    crop: Crop
    fps: int | None = None
    include_audio: bool = True


def export_args(media: Media, options: ExportOptions, output: Path) -> list[str]:
    validate_trim(options.start, options.end, media.duration)
    fmt = options.format
    if fmt not in VIDEO_FORMATS + AUDIO_FORMATS:
        raise ValueError('Unsupported export format.')
    audio_only = fmt in AUDIO_FORMATS
    if audio_only and not media.has_audio:
        raise ValueError('Audio-only export is unavailable: this video has no audio track.')
    # Accurate input seeking uses timestamps and decoder preroll, including VFR.
    args = ['-hide_banner', '-y', '-ss', f'{options.start:.6f}', '-i', str(media.path),
            '-t', f'{options.end - options.start:.6f}', '-map_metadata', '-1', '-sn', '-dn']
    audio_codecs = {'mp3': ['-c:a', 'libmp3lame', '-q:a', '2'],
                    'wav': ['-c:a', 'pcm_s16le'], 'flac': ['-c:a', 'flac'],
                    'm4a': ['-c:a', 'aac', '-b:a', '192k']}
    if audio_only:
        args += ['-map', '0:a:0', '-vn'] + audio_codecs[fmt]
    else:
        options.crop.validate(media.width, media.height)
        if options.fps is not None and options.fps not in RATES:
            raise ValueError('Choose one of the available output frame rates.')
        c = options.crop
        # exact=1 avoids implicit chroma rounding; pad preserves odd crop sizes.
        filters = [f'crop={c.width}:{c.height}:{c.x}:{c.y}:exact=1']
        if options.fps is not None:
            filters.append(f'fps={options.fps}')
        if fmt == 'gif':
            graph = f'[0:{media.video_index}]' + ','.join(filters) + ',split[a][b];[a]palettegen=stats_mode=diff[p];[b][p]paletteuse=dither=sierra2_4a[v]'
            args += ['-filter_complex', graph, '-map', '[v]', '-an', '-loop', '0']
        else:
            filters += ['pad=ceil(iw/2)*2:ceil(ih/2)*2', 'setsar=1']
            args += ['-map', f'0:{media.video_index}', '-vf', ','.join(filters)]
            if fmt == 'webm':
                args += ['-c:v', 'libvpx-vp9', '-crf', '32', '-b:v', '0', '-row-mt', '1']
            else:
                args += ['-c:v', 'libx264', '-preset', 'medium', '-crf', '20', '-movflags', '+faststart',
                         '-video_track_timescale', '1000000']
            args += ['-pix_fmt', 'yuv420p']
            if media.has_audio and options.include_audio:
                args += ['-map', '0:a:0', '-c:a', 'libopus' if fmt == 'webm' else 'aac', '-b:a', '192k']
            else:
                args += ['-an']
        # No -r and no fps filter for Same as source. Encoder time base follows
        # the demuxer, avoiding conversion to avg_frame_rate for VFR files.
        args += ['-fps_mode', 'vfr', '-enc_time_base:v', 'demux']
    args += ['-progress', 'pipe:1', '-nostats', str(output)]
    return args


class ExportTarget:
    """Stage beside destination, then atomically publish only on success."""
    def __init__(self, destination: Path, source: Path, overwrite: bool = False):
        self.destination = destination.resolve()
        if self.destination == source.resolve() or (self.destination.exists() and os.path.samefile(self.destination, source)):
            raise ValueError('Choose a different destination to preserve the original video.')
        self.overwrite = overwrite
        if self.destination.exists() and not overwrite:
            raise FileExistsError('The destination already exists.')
        fd, name = tempfile.mkstemp(prefix=STAGING_PREFIX, suffix='.' + destination.suffix.lstrip('.'), dir=self.destination.parent)
        os.close(fd)
        self.temporary = Path(name)

    def commit(self) -> None:
        if not self.temporary.exists() or self.temporary.stat().st_size == 0:
            raise RuntimeError('FFmpeg produced an empty output.')
        if self.overwrite:
            os.replace(self.temporary, self.destination)
        elif os.name == 'nt':
            os.rename(self.temporary, self.destination)  # Windows fails if destination exists.
        else:
            os.link(self.temporary, self.destination)
            self.temporary.unlink()

    def cleanup(self) -> None:
        self.temporary.unlink(missing_ok=True)
