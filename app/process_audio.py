"""Native process-loopback packets feed the existing timestamped PCM writers."""
import struct
import subprocess
import threading
from queue import Full

from .video_utils import executable


def read_exact(stream, size):
    parts = []
    while size:
        data = stream.read(size)
        if not data:
            raise EOFError('Application audio capture stopped.')
        parts.append(data)
        size -= len(data)
    return b''.join(parts)


class ProcessAudio:
    def __init__(self, capture):
        self.capture = capture
        self.process = None
        self.stopping = False
        self.ready = threading.Event()

    def start(self):
        self.process = subprocess.Popen([executable('seer-capture'), 'audio'] +
            [str(pid) for pid in self.capture.device.process_ids], stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=subprocess.CREATE_NO_WINDOW)
        self.thread = threading.Thread(target=self._read, daemon=True, name='Seer app audio')
        self.thread.start()
        if not self.ready.wait(15):
            self.stop()
            raise RuntimeError('Application audio capture timed out.')
        if self.capture.error:
            raise RuntimeError(self.capture.error)

    def _read(self):
        try:
            if read_exact(self.process.stdout, 4) != b'SEA1':
                raise RuntimeError('Invalid application audio format.')
            self.ready.set()
            while True:
                frames, timestamp = struct.unpack('<Iq', read_exact(self.process.stdout, 12))
                if frames < 1 or frames > 48000:
                    raise RuntimeError('Invalid application audio packet.')
                data = read_exact(self.process.stdout, frames * 4)
                stamp = timestamp / 10000000
                if self.capture.first_time is None:
                    self.capture.first_time = stamp
                self.capture.queue.put_nowait((data, stamp))
        except (EOFError, OSError, RuntimeError, Full) as error:
            if not self.stopping:
                self.process.kill()
                detail = self.process.stderr.read().decode('utf-8', 'replace').strip()
                self.capture.error = detail or f'Application audio capture failed: {error}'
        finally:
            self.ready.set()

    def stop(self):
        self.stopping = True
        if self.process is not None:
            try:
                self.process.stdin.write(b'q\n')
                self.process.stdin.flush()
                self.process.stdin.close()
                self.process.wait(timeout=2)
            except (OSError, subprocess.TimeoutExpired):
                self.process.kill()
                self.process.wait()
            self.thread.join(timeout=2)
            for stream in (self.process.stdout, self.process.stderr):
                stream.close()
