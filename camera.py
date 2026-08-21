"""Photo capture using picamera2 (modern libcamera stack), with a live
preview stream during the countdown so guests can see themselves.

Falls back to a mock camera that generates animated placeholder frames
when picamera2 isn't available (e.g. developing on a non-Pi machine), so
the rest of the pipeline (preview UI, collage, S3 upload, QR) can be
built/tested off-Pi.
"""

import math
import os
import time
import uuid

from PIL import Image, ImageDraw

try:
    from picamera2 import Picamera2

    HAVE_PICAMERA2 = True
except (ImportError, RuntimeError):
    HAVE_PICAMERA2 = False

COUNTDOWN_FROM = 3
HOLD_SECONDS = 1.0  # how long each just-taken photo stays up before the next countdown


def _fit(size, box):
    scale = min(box[0] / size[0], box[1] / size[1])
    return (max(1, int(size[0] * scale)), max(1, int(size[1] * scale)))


def _apply_logo(image_path, logo_path):
    if not logo_path or not os.path.exists(logo_path):
        return
    photo = Image.open(image_path).convert("RGBA")
    logo = Image.open(logo_path).convert("RGBA")
    logo_resized = logo.resize((int(photo.width * 0.25), int(photo.height * 0.25)))
    margin = 30
    position = (
        photo.width - logo_resized.width - margin,
        photo.height - logo_resized.height - margin,
    )
    photo.paste(logo_resized, position, logo_resized)
    photo.convert("RGB").save(image_path)


def _run_sequence(
    num_images, grab_frame, take_photo, on_countdown, on_preview, on_shot, warmup, hold
):
    """The countdown/shutter choreography shared by the real and mock cameras.

    grab_frame() -> PIL.Image          a live, preview-sized frame
    take_photo(i) -> (path, PIL.Image) full-res capture to disk + preview-sized copy
    """

    def stream_for(seconds):
        # Pump live frames to the UI until `seconds` have elapsed.
        deadline = time.monotonic() + seconds
        if on_preview:
            while time.monotonic() < deadline:
                on_preview(grab_frame())
        else:
            time.sleep(seconds)

    image_files = []
    stream_for(warmup)  # let auto-exposure/white-balance settle, showing live view meanwhile
    for i in range(num_images):
        if on_shot:
            on_shot(i + 1, num_images)
        for count in range(COUNTDOWN_FROM, 0, -1):
            if on_countdown:
                on_countdown(count)
            stream_for(1.0)
        if on_countdown:
            on_countdown(None)
        path, shot = take_photo(i)
        image_files.append(path)
        if on_preview:
            on_preview(shot)  # hold the captured photo so guests see what was taken
            time.sleep(hold)
    return image_files


def _capture_with_picamera2(
    num_images, resolution, preview_size, on_countdown, on_preview, on_shot, logo_path, session_id
):
    picam2 = Picamera2()
    # A *video* configuration streams frames fast enough for a smooth live
    # preview (a still configuration flushes the whole pipeline per frame --
    # only ~5fps on a Pi 3A+). capture_file() still writes a full main-stream
    # JPEG for the actual photo, so preview and photo share one FOV. The
    # lores stream feeds the preview cheaply so grabbing a frame doesn't wait
    # on the full-res main buffer.
    # Capture the preview from a deliberately small lores stream: the
    # per-frame YUV->RGB conversion cost scales with its pixel count, so a
    # ~640px-wide stream previews far faster on a Pi 3A+ than a full-size
    # one, then we upscale the little frame to the display size (cheap).
    lores_size = _fit(preview_size, (640, 360))
    config = picam2.create_video_configuration(
        main={"size": resolution},
        lores={"size": lores_size},
        display=None,
        buffer_count=4,
    )
    picam2.configure(config)
    picam2.start()

    os.makedirs("pics", exist_ok=True)

    def grab_frame():
        # lores is YUV420; capture_image handles the conversion to RGB.
        return picam2.capture_image("lores").resize(preview_size, Image.BILINEAR)

    def take_photo(i):
        path = f"pics/{session_id}-{i}.jpg"
        picam2.capture_file(path)
        _apply_logo(path, logo_path)
        shot = Image.open(path).resize(preview_size, Image.BILINEAR)
        return path, shot

    try:
        return _run_sequence(
            num_images, grab_frame, take_photo, on_countdown, on_preview, on_shot,
            warmup=1.5, hold=HOLD_SECONDS,
        )
    finally:
        picam2.stop()
        picam2.close()


def _capture_with_mock(
    num_images, resolution, preview_size, on_countdown, on_preview, on_shot, logo_path, session_id
):
    os.makedirs("pics", exist_ok=True)
    t0 = time.monotonic()

    def synth(size, label):
        # A moving block so the mock preview visibly animates.
        w, h = size
        t = time.monotonic() - t0
        img = Image.new("RGB", size, (30, 60, 110))
        d = ImageDraw.Draw(img)
        x = int((w - w // 8) * (0.5 + 0.5 * math.sin(t * 2)))
        d.rectangle([x, h // 2 - h // 10, x + w // 8, h // 2 + h // 10], fill=(240, 200, 60))
        d.text((20, 20), label, fill="white")
        return img

    def grab_frame():
        time.sleep(0.03)
        return synth(preview_size, "MOCK PREVIEW")

    def take_photo(i):
        path = f"pics/{session_id}-{i}.jpg"
        synth(resolution, f"MOCK PHOTO {i + 1}").save(path)
        _apply_logo(path, logo_path)
        shot = Image.open(path).resize(preview_size, Image.BILINEAR)
        return path, shot

    return _run_sequence(
        num_images, grab_frame, take_photo, on_countdown, on_preview, on_shot,
        warmup=0.3, hold=0.5,
    )


def capture_images(
    num_images,
    resolution=(1920, 1080),
    on_countdown=None,
    on_preview=None,
    on_shot=None,
    logo_path=None,
    session_id=None,
    preview_size=None,
):
    """Capture `num_images` photos. Returns a list of local file paths,
    named pics/<session_id>-<n>.jpg so concurrent/same-day sessions never
    overwrite each other's files (important for the upload-retry queue).

    Callbacks (all optional, all invoked on the calling thread):
      on_shot(i, n)       before photo i of n begins its countdown
      on_countdown(c)     with 3, 2, 1, then None right before each shutter
      on_preview(img)     repeatedly with live preview-sized frames during the
                          countdown, then once with the captured photo, which
                          is held on screen briefly before the next countdown
    preview_size defaults to a box of 960x540 matching the capture aspect.
    """
    session_id = session_id or uuid.uuid4().hex[:8]
    preview_size = preview_size or _fit(resolution, (960, 540))
    impl = _capture_with_picamera2 if HAVE_PICAMERA2 else _capture_with_mock
    return impl(
        num_images, resolution, preview_size, on_countdown, on_preview, on_shot, logo_path, session_id
    )
