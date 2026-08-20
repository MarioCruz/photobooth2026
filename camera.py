"""Photo capture using picamera2 (modern libcamera stack).

Falls back to a mock camera that generates placeholder JPEGs when
picamera2 isn't available (e.g. developing on a non-Pi machine), so the
rest of the pipeline (S3 upload, gallery, QR) can be built/tested off-Pi.
"""

import os
import time
from datetime import date

from PIL import Image, ImageDraw

try:
    from picamera2 import Picamera2

    HAVE_PICAMERA2 = True
except (ImportError, RuntimeError):
    HAVE_PICAMERA2 = False


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


def _capture_with_picamera2(num_images, resolution, on_countdown, logo_path):
    picam2 = Picamera2()
    config = picam2.create_still_configuration(main={"size": resolution})
    picam2.configure(config)
    picam2.start()
    time.sleep(2)  # let auto-exposure/white-balance settle

    os.makedirs("pics", exist_ok=True)
    today = date.today().strftime("%Y-%m-%d")
    image_files = []

    try:
        for i in range(num_images):
            for count in (3, 2, 1):
                if on_countdown:
                    on_countdown(count)
                time.sleep(1)
            if on_countdown:
                on_countdown(None)

            image_file = f"pics/booth_{today}_{i}.jpg"
            picam2.capture_file(image_file)
            _apply_logo(image_file, logo_path)
            image_files.append(image_file)
    finally:
        picam2.stop()

    return image_files


def _capture_with_mock(num_images, resolution, on_countdown, logo_path):
    os.makedirs("pics", exist_ok=True)
    today = date.today().strftime("%Y-%m-%d")
    image_files = []

    for i in range(num_images):
        for count in (3, 2, 1):
            if on_countdown:
                on_countdown(count)
            time.sleep(0.2)
        if on_countdown:
            on_countdown(None)

        image_file = f"pics/booth_{today}_{i}.jpg"
        img = Image.new("RGB", resolution, color=(40 + i * 40, 90, 160))
        draw = ImageDraw.Draw(img)
        draw.text((40, 40), f"MOCK PHOTO {i + 1}", fill="white")
        img.save(image_file)
        _apply_logo(image_file, logo_path)
        image_files.append(image_file)

    return image_files


def capture_images(num_images, resolution=(1920, 1080), on_countdown=None, logo_path=None):
    """Capture `num_images` photos. Returns a list of local file paths.

    on_countdown(n): optional callback invoked with 3, 2, 1, then None
    right before each shutter, so the UI can display a countdown.
    """
    if HAVE_PICAMERA2:
        return _capture_with_picamera2(num_images, resolution, on_countdown, logo_path)
    return _capture_with_mock(num_images, resolution, on_countdown, logo_path)
