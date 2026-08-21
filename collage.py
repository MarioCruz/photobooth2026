"""Arrange a session's captured photos into a single grid image, shown
on-screen right after capture, before the QR code."""

import math

from PIL import Image


def make_collage(image_paths, max_size, gap=12, bg=(20, 20, 20)):
    """Return a single PIL Image of exactly max_size with image_paths
    arranged in a roughly square grid (2x2 for 4 photos). Photos are
    scaled to a common size and the grid is packed tightly and centered,
    rather than floating each photo in an oversized cell."""
    images = [Image.open(p) for p in image_paths]
    count = len(images)
    cols = math.ceil(math.sqrt(count))
    rows = math.ceil(count / cols)

    max_w, max_h = max_size
    cell_w = (max_w - gap * (cols + 1)) // cols
    cell_h = (max_h - gap * (rows + 1)) // rows

    # Common thumbnail size: the largest that fits every photo's aspect in a cell.
    scale = min(min(cell_w / im.width, cell_h / im.height) for im in images)
    thumb_w = max(1, int(images[0].width * scale))
    thumb_h = max(1, int(images[0].height * scale))
    for im in images[1:]:
        thumb_w = min(thumb_w, max(1, int(im.width * scale)))
        thumb_h = min(thumb_h, max(1, int(im.height * scale)))

    grid_w = cols * thumb_w + (cols - 1) * gap
    grid_h = rows * thumb_h + (rows - 1) * gap
    x0 = (max_w - grid_w) // 2
    y0 = (max_h - grid_h) // 2

    collage = Image.new("RGB", (max_w, max_h), bg)
    for i, img in enumerate(images):
        thumb = img.copy()
        thumb.thumbnail((thumb_w, thumb_h))
        col, row = i % cols, i // cols
        x = x0 + col * (thumb_w + gap) + (thumb_w - thumb.width) // 2
        y = y0 + row * (thumb_h + gap) + (thumb_h - thumb.height) // 2
        collage.paste(thumb, (x, y))

    return collage
