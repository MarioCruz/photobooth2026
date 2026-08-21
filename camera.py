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

from collage import load_scaled

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
    try:
        # Two modes, switched at the shutter:
        #
        # preview -- a light *video* configuration that streams smoothly (a
        #   still configuration flushes the whole pipeline per frame, ~5fps on
        #   a Pi 3A+). Its main stream is deliberately small and RGB888: the
        #   per-frame cost scales with pixel count, and the pipeline's lores
        #   stream is YUV420, which PIL can't take without a conversion that
        #   costs more than it saves. The small frame is upscaled to display
        #   size afterwards, which is cheap.
        # still -- the full sensor resolution, used only for the actual photo.
        #   Streaming this continuously would exhaust the Pi's CMA pool, so we
        #   switch into it per shot and drop straight back to the preview mode.
        #
        # Both are the same aspect ratio, so the live preview frames what the
        # photo will actually capture.
        preview_config = picam2.create_video_configuration(
            main={"size": _fit(resolution, (640, 480)), "format": "RGB888"},
            display=None,
            buffer_count=4,
        )
        still_config = picam2.create_still_configuration(main={"size": resolution}, buffer_count=1)

        os.makedirs("pics", exist_ok=True)

        def grab_frame():
            return picam2.capture_image("main").resize(preview_size, Image.BILINEAR)

        def take_photo(i):
            path = f"pics/{session_id}-{i}.jpg"
            picam2.switch_mode_and_capture_file(still_config, path)
            _apply_logo(path, logo_path)
            shot = load_scaled(path, preview_size)  # never decodes the full 8MP frame
            shot.thumbnail(preview_size)
            return path, shot

        picam2.configure(preview_config)
        picam2.start()
        return _run_sequence(
            num_images, grab_frame, take_photo, on_countdown, on_preview, on_shot,
            warmup=1.5, hold=HOLD_SECONDS,
        )
    finally:
        # Always release the camera, including failures while constructing
        # configurations or during configure()/start(). Calling stop() on a
        # partially started camera is safe to attempt; close() still runs if
        # stop() itself fails.
        try:
            picam2.stop()
        except Exception:
            pass
        finally:
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
