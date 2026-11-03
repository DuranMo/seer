import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication
import pytest

from app.thumbnails import Thumbnails, cache_path
from app.video_utils import executable, run_checked


def wait_for_idle(loader):
    loop = QEventLoop()
    loader.idle.connect(loop.quit)
    timer = QTimer()
    timer.setSingleShot(True)
    timer.timeout.connect(loop.quit)
    timer.start(12000)
    loop.exec()
    timer.stop()
    loader.idle.disconnect(loop.quit)
    assert loader.current is None


def test_thumbnail_real_frame_cache_reuse_and_source_change(tmp_path):
    app = QApplication.instance() or QApplication([])
    try:
        tool = executable('ffmpeg')
    except RuntimeError as error:
        pytest.skip(str(error))
    source = tmp_path / "clip with a space's.mp4"
    run_checked([tool, '-hide_banner', '-v', 'error', '-y', '-f', 'lavfi', '-i',
                 'color=red:size=320x240:duration=0.1', '-c:v', 'libx264', str(source)])
    loader = Thumbnails(cache=tmp_path / 'cache')
    result = []
    loader.ready.connect(lambda path, image: result.append((path, image)))
    assert loader.request(source) is None
    wait_for_idle(loader)
    assert len(result) == 1
    path, image = result[0]
    assert path == source
    frame = QImage(str(image))
    assert frame.width() == 320 and frame.height() == 180
    assert frame.pixelColor(160, 90).red() > 200
    assert frame.pixelColor(0, 90).red() < 10  # Aspect ratio retained, padded sides.
    assert loader.request(source) == image
    assert loader.current is None  # Cached browsing doesn't restart FFmpeg.
    source.write_bytes(source.read_bytes() + b'changed')
    assert cache_path(source, loader.cache) != image
    assert not list(loader.cache.glob('*.tmp.png'))
    loader.stop()


def test_broken_clip_fails_once_and_leaves_no_partial_thumbnail(tmp_path):
    app = QApplication.instance() or QApplication([])
    try:
        executable('ffmpeg')
    except RuntimeError as error:
        pytest.skip(str(error))
    source = tmp_path / 'broken.mp4'
    source.write_bytes(b'not a video')
    loader = Thumbnails(cache=tmp_path / 'cache')
    loader.request(source)
    wait_for_idle(loader)
    assert cache_path(source, loader.cache) in loader.failed
    assert loader.request(source) is None and loader.current is None
    assert not list(loader.cache.iterdir())
    loader.stop()
