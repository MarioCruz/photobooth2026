#!/usr/bin/env python3
"""Event photo booth: capture photos, upload to S3, show a QR code so
guests can retrieve them from a web gallery. No email, no Twitter/X."""

import configparser
import tkinter as tk
from tkinter import messagebox

from PIL import Image, ImageTk

from camera import capture_images
from gallery import GalleryUploader
from qr import make_qr_image

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


def on_take_photos():
    hide_qr()
    button_take_photos.config(state=tk.DISABLED, text="Taking photos…")
    window.update()

    try:
        image_files = capture_images(
            NUM_PHOTOS, resolution=RESOLUTION, on_countdown=on_countdown, logo_path=LOGO_PATH
        )
        button_take_photos.config(text="Uploading…")
        window.update()

        gallery_url = uploader.upload_session(image_files)
        show_qr(gallery_url)
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

qr_frame = tk.Frame(window)
qr_label = tk.Label(qr_frame)
qr_label.pack()
url_label = tk.Label(qr_frame, font=("Helvetica", 11), fg="gray")
url_label.pack()
scan_label = tk.Label(qr_frame, text="Scan to view & download your photos", font=("Helvetica", 13, "bold"))
scan_label.pack(before=qr_label)

window.geometry("1024x768")
window.eval("tk::PlaceWindow . center")

window.mainloop()
