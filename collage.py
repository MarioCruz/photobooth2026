"""Arrange a session's captured photos into a single grid image, shown
on-screen right after capture, before the QR code."""

import math

from PIL import Image


def make_collage(image_paths, max_size, gap=12, bg=(20, 20, 20)):
    """Return a single PIL Image with image_paths arranged in a roughly
    square grid (2x2 for 4 photos), scaled to fit within max_size."""
    images = [Image.open(p) for p in image_paths]
    count = len(images)
    cols = math.ceil(math.sqrt(count))
    rows = math.ceil(count / cols)

    max_w, max_h = max_size
    cell_w = (max_w - gap * (cols + 1)) // cols
    cell_h = (max_h - gap * (rows + 1)) // rows

    collage = Image.new("RGB", (max_w, max_h), bg)
    for i, img in enumerate(images):
        thumb = img.copy()
        thumb.thumbnail((cell_w, cell_h))
        col, row = i % cols, i // cols
        x = gap + col * (cell_w + gap) + (cell_w - thumb.width) // 2
        y = gap + row * (cell_h + gap) + (cell_h - thumb.height) // 2
        collage.paste(thumb, (x, y))

    return collage
