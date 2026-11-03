import array
import math
from pathlib import Path
import subprocess
import sys
import time
import wave
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.audio import AudioCapture, AudioDevice


def main():
    folder = Path('.tools/process-audio-check').resolve()
    folder.mkdir(parents=True, exist_ok=True)
    processes = []
    try:
        for hz in (440, 880):
            path = folder / f'tone-{hz}.wav'
            values = array.array('h', (int(9000 * math.sin(2 * math.pi * hz * i / 48000)) for i in range(48000) for _ in range(2)))
            with wave.open(str(path), 'wb') as out:
                out.setnchannels(2); out.setsampwidth(2); out.setframerate(48000); out.writeframes(values.tobytes())
            code = 'import winsound,sys,time; winsound.PlaySound(sys.argv[1], winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_LOOP); time.sleep(20)'
            processes.append(subprocess.Popen([sys.executable, '-c', code, str(path)], creationflags=subprocess.CREATE_NO_WINDOW))
        time.sleep(0.4)
        for selected in ((processes[0].pid,), tuple(p.pid for p in processes)):
            path = folder / ('one.wav' if len(selected) == 1 else 'both.wav')
            capture = AudioCapture(AudioDevice('apps', 0, 'test apps', 2, 48000, selected), path)
            capture.start()
            time.sleep(1.8)
            capture.stop()
            if capture.error: raise RuntimeError(capture.error)
            with wave.open(str(path), 'rb') as source:
                samples = array.array('h', source.readframes(source.getnframes()))[::2]
            samples = samples[12000:60000]
            energy = sum(v*v for v in samples) / len(samples)
            powers = {}
            for hz in (440, 880):
                a = 2 * sum(v * math.sin(2 * math.pi * hz * i / 48000) for i,v in enumerate(samples)) / len(samples)
                b = 2 * sum(v * math.cos(2 * math.pi * hz * i / 48000) for i,v in enumerate(samples)) / len(samples)
                powers[hz] = (a*a+b*b)/2
            print({'selected_apps': len(selected), 'samples': len(samples), 'power': powers, 'energy': energy}, flush=True)
            assert powers[440] > 10000, 'Selected app is silent.'
            if len(selected) == 1: assert powers[880] < powers[440] * 0.001, 'Unselected app leaked into the capture.'
            else: assert powers[880] > powers[440] * 0.5, 'Second selected app missing.'
        print('OK: process isolation, two-app audio mixing, and shutdown.', flush=True)
    finally:
        for process in processes:
            process.terminate()
            process.wait()


if __name__ == '__main__':
    main()
