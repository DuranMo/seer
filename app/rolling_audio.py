"""Bounded WASAPI PCM segments, aligned to ADC timestamps rather than callbacks."""
from __future__ import annotations

from pathlib import Path
from queue import Empty
import time
import wave

from .audio import AudioCapture, AudioDevice
from .segments import Segment, SegmentRing


class RollingAudio(AudioCapture):
    def __init__(self, device: AudioDevice, folder: Path, ring: SegmentRing):
        super().__init__(device, folder / 'unused.wav')
        self.folder, self.ring = folder, ring
        self.origin = time.time()

    def _write(self):
        out = None
        written = 0
        rate, channels = self.device.rate, self.device.channels
        block_size = channels * 2
        index = 0
        path = None

        def close():
            nonlocal out
            if out is not None:
                out.close()
                if written:
                    self.ring.add(Segment(path, self.origin + (self.samples - written) / rate,
                                          self.origin + self.samples / rate))
                out = None

        def write(data: bytes | None, frames: int):
            nonlocal out, written, index, path
            offset = 0
            while frames:
                if out is None:
                    path = self.folder / f'a-{index:010d}.wav'
                    index += 1
                    out = wave.open(str(path), 'wb')
                    out.setnchannels(channels)
                    out.setsampwidth(2)
                    out.setframerate(rate)
                    written = 0
                count = min(frames, rate - written)
                chunk = b'\0' * (count * block_size) if data is None else data[offset * block_size:(offset + count) * block_size]
                out.writeframesraw(chunk)
                written += count
                self.samples += count
                offset += count
                frames -= count
                if written == rate:
                    close()
                    self.ring.prune(time.time())

        try:
            while True:
                try:
                    item = self.queue.get(timeout=0.05)
                except Empty:
                    # Loopback may send no callbacks during silence. Keep its
                    # timestamp timeline alive, allowing for normal device latency.
                    target = max(0, int((time.time() - self.origin - 0.5) * rate))
                    write(None, max(0, target - self.samples))
                    continue
                if item is None:
                    target = max(0, int((time.time() - self.origin) * rate))
                    write(None, max(0, target - self.samples))
                    break
                data, stamp = item
                target = round((stamp - self.origin) * rate)
                frames = len(data) // block_size
                if target > self.samples:
                    write(None, target - self.samples)
                overlap = max(0, self.samples - target)
                if overlap > rate * 0.1 and frames > 0 and target >= 0:
                    self.error = 'Audio arrived too late to keep replay synchronized. Restart replay or choose another audio device.'
                    break
                skip = min(frames, overlap)
                if frames > skip:
                    write(data[skip * block_size:], frames - skip)
        except Exception as error:
            self.error = f'Rolling audio capture failed: {error}'
        finally:
            close()


def assemble_audio(segments: list[Segment], start: float, end: float,
                   device: AudioDevice, output: Path):
    rate, stride = device.rate, device.channels * 2
    total = max(0, round((end - start) * rate))
    position = 0
    with wave.open(str(output), 'wb') as out:
        out.setnchannels(device.channels)
        out.setsampwidth(2)
        out.setframerate(rate)

        def silence(frames):
            while frames:
                count = min(rate, frames)
                out.writeframesraw(b'\0' * count * stride)
                frames -= count

        for segment in segments:
            target = max(0, round((segment.start - start) * rate))
            if target >= total:
                break
            if target > position:
                silence(target - position)
                position = target
            skip = max(0, round((start - segment.start) * rate), position - round((segment.start - start) * rate))
            with wave.open(str(segment.path), 'rb') as source:
                source.setpos(min(skip, source.getnframes()))
                remaining = min(source.getnframes() - source.tell(), total - position)
                while remaining:
                    count = min(rate, remaining)
                    data = source.readframes(count)
                    if not data:
                        raise RuntimeError('An audio buffer segment was truncated.')
                    out.writeframesraw(data)
                    actual = len(data) // stride
                    position += actual
                    remaining -= actual
        silence(total - position)
