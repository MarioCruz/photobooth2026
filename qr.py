"""QR code generation for gallery URLs."""

import qrcode


def make_qr_image(url, box_size=8, border=2):
    qr = qrcode.QRCode(box_size=box_size, border=border)
    qr.add_data(url)
    qr.make(fit=True)
    return qr.make_image(fill_color="black", back_color="white").convert("RGB")
