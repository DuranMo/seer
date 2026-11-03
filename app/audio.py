from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from queue import Queue, Full
import threading
import time
import wave


@dataclass(frozen=True)
class AudioDevice:
    mode: str
    index: int
    name: str
    channels: int
    rate: int


def devices() -> tuple[list[AudioDevice], str]:
    """Enumerate real WASAPI inputs and speaker loopbacks, never FFmpeg guesses."""
    try:
        import pyaudiowpatch as pa
        with pa.PyAudio() as audio:
            host = audio.get_host_api_info_by_type(pa.paWASAPI)
            found = []
            for info in audio.get_device_info_generator():
                if info['hostApi'] != host['index'] or info['maxInputChannels'] < 1:
                    continue
                found.append(AudioDevice('system' if info.get('isLoopbackDevice') else 'microphone',
                    int(info['index']), info['name'], min(2, int(info['maxInputChannels'])),
                    int(info['defaultSampleRate'])))
            return found, '' if found else 'No WASAPI input or loopback devices are available.'
    except (ImportError, OSError, RuntimeError) as error:
        return [], f'Windows audio capture unavailable: {error}'


class AudioCapture:
    """PortAudio callback clock + bounded disk queue; gaps retain wall-clock timing."""
    def __init__(self, device: AudioDevice, path: Path):
        self.device, self.path = device, path
        self.first_time: float | None = None
        self.error = ''
        self.audio = self.stream = None
        self.queue = Queue(maxsize=512)
        self.thread = None
        self.samples = 0
        self.was_active = False

    def start(self):
        import pyaudiowpatch as pa
        self.audio = pa.PyAudio()
        try:
            info = self.audio.get_device_info_by_index(self.device.index)
            if info['name'] != self.device.name:
                raise RuntimeError('The selected audio device changed. Refresh audio devices and try again.')
            self.audio.is_format_supported(self.device.rate, input_device=self.device.index,
                input_channels=self.device.channels, input_format=pa.paInt16)
            self.stream = self.audio.open(format=pa.paInt16, channels=self.device.channels,
                rate=self.device.rate, input=True, input_device_index=self.device.index,
                frames_per_buffer=1024, stream_callback=self._callback, start=False)
            self.thread = threading.Thread(target=self._write, daemon=True)
            self.thread.start()
            self.stream.start_stream()
        except Exception:
            self.stop()
            raise

    def _callback(self, data, frames, clock, status):
        import pyaudiowpatch as pa
        # PortAudio ADC and current_time share a clock. Convert the sample's
        # capture timestamp to epoch time, as used by gdigrab's packet PTS.
        now = time.time()
        stamp = now + clock['input_buffer_adc_time'] - clock['current_time']
        if not stamp or abs(stamp - now) > 5:
            self.error = 'The audio device returned an invalid capture clock.'
            return None, pa.paAbort
        if self.first_time is None:
            self.first_time = stamp
        if status & pa.paInputOverflow:
            self.error = 'Audio input overflowed. Try a lower recording frame rate.'
            return None, pa.paAbort
        try:
            self.queue.put_nowait((data, stamp))
        except Full:
            self.error = 'Audio could not be written fast enough.'
            return None, pa.paAbort
        return None, pa.paContinue

    def _write(self):
        try:
            with wave.open(str(self.path), 'wb') as out:
                out.setnchannels(self.device.channels)
                out.setsampwidth(2)
                out.setframerate(self.device.rate)
                while True:
                    item = self.queue.get()
                    if item is None:
                        break
                    data, stamp = item
                    target = round((stamp - self.first_time) * self.device.rate)
                    # Loopback may deliver no samples during silence. Insert the
                    # measured gap rather than pulling later sound forward.
                    gap = max(0, target - self.samples)
                    if gap > self.device.rate * 0.02:
                        while gap:
                            count = min(gap, self.device.rate)
                            out.writeframesraw(b'\0' * (count * self.device.channels * 2))
                            self.samples += count
                            gap -= count
                    out.writeframesraw(data)
                    self.samples += len(data) // (self.device.channels * 2)
        except Exception as error:
            self.error = f'Audio file could not be written: {error}'

    def stop(self):
        errors = []
        if self.stream:
            try:
                self.was_active = self.stream.is_active()
                self.stream.stop_stream()
            except Exception as error:
                errors.append(str(error))
            finally:
                try:
                    self.stream.close()
                except Exception as error:
                    errors.append(str(error))
                self.stream = None
        if self.audio:
            try:
                self.audio.terminate()
            except Exception as error:
                errors.append(str(error))
            self.audio = None
        if self.thread:
            # Writer drains asynchronously; its only slow operation is disk IO.
            while self.thread.is_alive():
                try:
                    self.queue.put(None, timeout=0.1)
                    break
                except Full:
                    continue
            self.thread.join()
            self.thread = None
        if errors and not self.error:
            self.error = 'Audio capture could not stop cleanly: ' + '; '.join(errors)


def mux_args(video: Path, audio: Path, output: Path, offset: float) -> list[str]:
    # Offset is measured from the first actual video frame, not process launch.
    return ['-hide_banner', '-y', '-i', str(video), '-itsoffset', f'{offset:.6f}',
        '-i', str(audio), '-map', '0:v:0', '-map', '1:a:0', '-c:v', 'copy',
        '-af', 'aresample=async=1000:first_pts=0,apad', '-c:a', 'aac', '-b:a', '192k',
        '-shortest', '-movflags', '+faststart', '-progress', 'pipe:1', str(output)]
