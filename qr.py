"""QR code generation for gallery URLs."""

import qrcode


def make_qr_image(url, box_size=8, border=2, size=None):
    """Render `url` as a QR code. If `size` (pixels) is given, pick the
    largest whole-pixel module size that fits so the code stays crisp --
    resampling a QR to an arbitrary size makes modules uneven and harder
    for phones to scan."""
    qr = qrcode.QRCode(box_size=box_size, border=border)
    qr.add_data(url)
    qr.make(fit=True)
    if size:
        qr.box_size = max(1, size // (qr.modules_count + 2 * border))
    return qr.make_image(fill_color="black", back_color="white").convert("RGB")
