from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import tempfile

from .audio import AudioDevice
from .editor_state import EditorState
from .exporter import ExportOptions, ExportTarget, export_args
from .rolling_audio import assemble_audio
from .segments import Segment, concat_manifest
from .video_utils import Crop, executable, probe, run_checked


@dataclass(frozen=True)
class ReplayResult:
    state: EditorState
    kind: str


def build_replay(video: list[Segment], audio: list[Segment], device: AudioDevice | None,
                 start: float, end: float, destination: Path, kind: str, save_exact=False) -> ReplayResult:
    if save_exact:
        with tempfile.TemporaryDirectory(prefix='seer-clip-save-') as working:
            source = build_replay(video, audio, device, start, end, Path(working) / 'source.mp4', kind)
            state = source.state
            target = ExportTarget(destination, state.media.path)
            try:
                options = ExportOptions('mp4', state.start, state.end,
                    Crop(0, 0, state.media.width, state.media.height), include_audio=state.media.has_audio)
                args = export_args(state.media, options, target.temporary)
                args[args.index('-preset') + 1] = 'veryfast'
                args[-1:-1] = ['-threads', '2']
                run_checked([executable('ffmpeg')] + args, timeout=300)
                media = probe(target.temporary)
                target.commit()
                from dataclasses import replace
                media = replace(media, path=destination.resolve())
                return ReplayResult(EditorState(media, False, 0, media.duration, 0,
                    (0, 0, 1, 1), 'mp4', None, media.has_audio), kind)
            finally:
                target.cleanup()
    if not video:
        raise RuntimeError('No captured video is available for this clip.')
    if any(abs(right.start - left.end) > 0.1 for left, right in zip(video, video[1:])):
        raise RuntimeError('Replay history contains a capture gap. This clip cannot be assembled safely.')
    base, limit = video[0].start, video[-1].end
    start, end = max(start, base), min(end, limit)
    if end <= start:
        raise RuntimeError('The requested clip has no captured frames.')
    # Copy keyframe-aligned video without re-encoding while capture continues.
    # The editor selects the exact requested timestamps for the final export.
    with tempfile.TemporaryDirectory(prefix='seer-clip-build-') as working:
        folder = Path(working)
        manifest = folder / 'video.ffconcat'
        manifest.write_text(concat_manifest(video), encoding='utf-8')
        args = [executable('ffmpeg'), '-hide_banner', '-y', '-v', 'error',
                '-f', 'concat', '-safe', '0', '-i', str(manifest)]
        if device:
            wav = folder / 'audio.wav'
            assemble_audio(audio, base, limit, device, wav)
            args += ['-i', str(wav), '-map', '0:v:0', '-map', '1:a:0', '-c:a', 'aac', '-b:a', '192k']
        else:
            args += ['-map', '0:v:0', '-an']
        args += ['-c:v', 'copy', '-t', f'{limit - base:.9f}', '-movflags', '+faststart', str(destination)]
        try:
            run_checked(args, timeout=180)
            media = probe(destination)
        except Exception:
            destination.unlink(missing_ok=True)
            raise
    cut_start = max(0, start - base)
    cut_end = min(media.duration, end - base)
    if cut_end <= cut_start:
        destination.unlink(missing_ok=True)
        raise RuntimeError('The resulting clip has no usable selected duration.')
    state = EditorState(media, True, cut_start, cut_end, round(cut_start * 1000),
                        (0, 0, 1, 1), 'mp4', None, media.has_audio)
    return ReplayResult(state, kind)
