#!/usr/bin/env python3
"""Event photo booth: capture photos, upload to S3, show a QR code so
guests can retrieve them from a web gallery. No email, no Twitter/X."""

import configparser
import os
import tkinter as tk
import uuid
from tkinter import messagebox

from PIL import Image, ImageTk

import pending
from camera import capture_images
from gallery import GalleryUploader
from qr import make_qr_image

RETRY_INTERVAL_MS = 2 * 60 * 1000  # re-attempt any queued uploads every 2 minutes

config = configparser.ConfigParser()
config.read("config.ini")

EVENT_TITLE = config.get("event", "title")
EVENT_HASHTAG = config.get("event", "hashtag", fallback="")

NUM_PHOTOS = config.getint("camera", "num_photos", fallback=3)
RESOLUTION = (
    config.getint("camera", "resolution_width", fallback=1920),
    config.getint("camera", "resolution_height", fallback=1080),
)
LOGO_PATH = config.get("camera", "logo_path", fallback="") or None

uploader = GalleryUploader(config)


def on_countdown(count):
    countdown_label.config(text=str(count) if count else "")
    window.update()


def show_qr(gallery_url):
    qr_img = make_qr_image(gallery_url).resize((320, 320))
    qr_photo = ImageTk.PhotoImage(qr_img)
    qr_label.config(image=qr_photo, text="")
    qr_label.image = qr_photo  # keep a reference so it isn't garbage collected
    url_label.config(text=gallery_url)
    qr_frame.pack(pady=10)


def hide_qr():
    qr_frame.pack_forget()
    qr_label.config(image="", text="")
    url_label.config(text="")


def update_pending_label():
    count = len(pending.load())
    pending_label.config(text=f"{count} session(s) waiting to upload…" if count else "")


def retry_pending_uploads():
    """Retry any sessions that failed to upload earlier. Photos are never
    lost on a failed upload -- they stay in pics/ and queued in
    pics/pending_uploads.json until this succeeds."""
    for record in pending.load():
        files = [f for f in record["files"] if os.path.exists(f)]
        if not files:
            pending.remove(record["session_id"])  # local files gone, nothing left to retry
            continue
        try:
            uploader.upload_session(
                files, session_id=record["session_id"], event_slug=record["event_slug"]
            )
            pending.remove(record["session_id"])
        except Exception:
            pass  # still no connection -- leave it queued, try again next interval
    update_pending_label()


def periodic_retry():
    retry_pending_uploads()
    window.after(RETRY_INTERVAL_MS, periodic_retry)


def on_take_photos():
    hide_qr()
    button_take_photos.config(state=tk.DISABLED, text="Taking photos…")
    window.update()

    session_id = uuid.uuid4().hex[:8]
    try:
        image_files = capture_images(
            NUM_PHOTOS,
            resolution=RESOLUTION,
            on_countdown=on_countdown,
            logo_path=LOGO_PATH,
            session_id=session_id,
        )
        button_take_photos.config(text="Uploading…")
        window.update()

        try:
            gallery_url = uploader.upload_session(image_files, session_id=session_id)
            show_qr(gallery_url)
        except Exception as upload_err:
            pending.add(session_id, uploader.event_slug, image_files)
            update_pending_label()
            messagebox.showwarning(
                "Saved, upload pending",
                "Photos are saved and will upload automatically once the "
                f"connection is back. ({upload_err})",
            )
    except Exception as e:
        messagebox.showerror("Error", f"Something went wrong: {e}")
    finally:
        countdown_label.config(text="")
        button_take_photos.config(state=tk.NORMAL, text="Take Photos")


# ---- UI ----

window = tk.Tk()
window.title("Photo Booth")

label_welcome = tk.Label(window, text=EVENT_TITLE, font=("Helvetica", 22, "bold"))
label_welcome.pack(pady=(20, 5), fill=tk.BOTH, anchor=tk.CENTER)

if EVENT_HASHTAG:
    label_hashtag = tk.Label(window, text=EVENT_HASHTAG, font=("Helvetica", 14))
    label_hashtag.pack(pady=(0, 10), anchor=tk.CENTER)

image = Image.open("mtm.png")
photo = ImageTk.PhotoImage(image)
label_image = tk.Label(window, image=photo)
label_image.pack(pady=10)

countdown_label = tk.Label(window, text="", font=("Helvetica", 60, "bold"), fg="blue")
countdown_label.pack(pady=10)

button_take_photos = tk.Button(
    window,
    text="Take Photos",
    font=("Helvetica", 15, "bold"),
    command=on_take_photos,
    bg="blue",
    fg="white",
)
button_take_photos.pack(padx=40, pady=20, fill=tk.BOTH, anchor=tk.CENTER)

pending_label = tk.Label(window, text="", font=("Helvetica", 11), fg="darkorange")
pending_label.pack(pady=(0, 5))

qr_frame = tk.Frame(window)
qr_label = tk.Label(qr_frame)
qr_label.pack()
url_label = tk.Label(qr_frame, font=("Helvetica", 11), fg="gray")
url_label.pack()
scan_label = tk.Label(qr_frame, text="Scan to view & download your photos", font=("Helvetica", 13, "bold"))
scan_label.pack(before=qr_label)

window.geometry("1024x768")
window.eval("tk::PlaceWindow . center")

update_pending_label()
window.after(2000, periodic_retry)  # give the app a moment to draw first

window.mainloop()
