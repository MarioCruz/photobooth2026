"""Everything drawn on the kiosk's main "stage" area, rendered as PIL
images of exactly the stage size. Rendering whole screens as one image
means the Tk layout never reflows and nothing can overflow the display,
whatever state the booth is in. Pure PIL, so it's testable off-Pi."""

import os
from functools import lru_cache

from PIL import Image, ImageDraw, ImageFont

from collage import make_collage
from qr import make_qr_image

BG = (17, 17, 17)
FG = (245, 245, 245)
MUTED = (154, 154, 154)

_FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",  # Raspberry Pi OS
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",  # macOS, for off-Pi dev
]


@lru_cache(maxsize=None)
def font(size):
    for path in _FONT_CANDIDATES:
        if os.path.exists(path):
            return ImageFont.truetype(path, size)
    try:
        return ImageFont.load_default(size=size)  # Pillow >= 10.1
    except TypeError:
        return ImageFont.load_default()


def fit(size, box):
    """Largest (w, h) with `size`'s aspect ratio that fits inside `box`."""
    scale = min(box[0] / size[0], box[1] / size[1])
    return (max(1, int(size[0] * scale)), max(1, int(size[1] * scale)))


def _text_centered(draw, center, text, fnt, fill, stroke=0, stroke_fill=None):
    left, top, right, bottom = draw.textbbox((0, 0), text, font=fnt, stroke_width=stroke)
    x = center[0] - (right - left) / 2 - left
    y = center[1] - (bottom - top) / 2 - top
    draw.text((x, y), text, font=fnt, fill=fill, stroke_width=stroke, stroke_fill=stroke_fill)


def _paste_centered(canvas, img, center):
    canvas.paste(img, (int(center[0] - img.width / 2), int(center[1] - img.height / 2)))


def render_qr_screen(stage, url, heading, caption, lift=0):
    """A QR code on a white card, heading above, caption + URL below.
    Used both for the idle 'whole party' gallery and a guest's own session.

    lift raises the card (and its caption) by that many pixels, so the code
    sits at a comfortable height to scan rather than dead centre."""
    w, h = stage
    img = Image.new("RGB", stage, BG)
    d = ImageDraw.Draw(img)

    qr = make_qr_image(url, size=int(h * 0.58))
    pad = qr.width // 16
    card = qr.width + 2 * pad
    cx, cy = w // 2, h // 2 + h // 40 - lift
    d.rounded_rectangle(
        [cx - card // 2, cy - card // 2, cx + card // 2, cy + card // 2], radius=pad, fill="white"
    )
    _paste_centered(img, qr, (cx, cy))

    # Heading stays put; caption/URL follow the card up so the grouping holds.
    _text_centered(d, (cx, h // 14), heading, font(h // 18), FG)
    _text_centered(d, (cx, h - h // 9 - lift), caption, font(h // 30), FG)
    _text_centered(d, (cx, h - h // 22 - lift), url, font(h // 45), MUTED)
    return img


def render_preview(stage, frame, count=None, label=""):
    """Live camera frame (or the just-taken photo) with the countdown number
    and a small 'Photo i of n' label overlaid. With no frame yet, shows the
    label large in the middle instead (e.g. 'Get ready!')."""
    w, h = stage
    img = Image.new("RGB", stage, BG)
    d = ImageDraw.Draw(img)
    if frame is None:
        _text_centered(d, (w // 2, h // 2), label or "Get ready!", font(h // 10), FG)
        return img

    _paste_centered(img, frame, (w // 2, h // 2))
    if label:
        _text_centered(d, (w // 2, h // 14), label, font(h // 20), "white", stroke=2, stroke_fill="black")
    if count is not None:
        _text_centered(
            d, (w // 2, h // 2), str(count), font(int(h * 0.55)), "white",
            stroke=max(4, h // 70), stroke_fill="black",
        )
    return img


def render_collage(stage, image_files):
    """The session's photos in a grid filling the stage."""
    return make_collage(image_files, max_size=stage, gap=max(8, stage[1] // 60), bg=BG)
