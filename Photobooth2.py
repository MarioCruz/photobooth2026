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
from collage import make_collage
from gallery import GalleryUploader
from qr import make_qr_image

RETRY_INTERVAL_MS = 2 * 60 * 1000  # re-attempt any queued uploads every 2 minutes
COLLAGE_DISPLAY_MS = 15 * 1000  # show the just-taken photos before the QR code
SESSION_QR_DISPLAY_MS = 15 * 1000  # then show that guest's QR before returning to idle

config = configparser.ConfigParser()
config.read("config.ini")

EVENT_TITLE = config.get("event", "title")
EVENT_HASHTAG = config.get("event", "hashtag", fallback="")

NUM_PHOTOS = config.getint("camera", "num_photos", fallback=4)
RESOLUTION = (
    config.getint("camera", "resolution_width", fallback=1920),
    config.getint("camera", "resolution_height", fallback=1080),
)
LOGO_PATH = config.get("camera", "logo_path", fallback="") or None
BUTTON_PIN = config.getint("camera", "button_pin", fallback=17)

uploader = GalleryUploader(config)
PARTY_GALLERY_URL = f"{uploader.website_base_url}/?event={uploader.event_slug}"

try:
    from gpiozero import Button as GPIOButton

    # Physical trigger button: signal on BCM pin BUTTON_PIN (default
    # GPIO17 / physical pin 11), other leg to any GND (e.g. physical
    # pin 9). Internal pull-up means a plain push-to-make button needs
    # no external resistor. Falls back to on-screen-only if no button
    # is wired (e.g. developing off-Pi).
    capture_button = GPIOButton(BUTTON_PIN, bounce_time=0.2)
except Exception:
    capture_button = None


def on_countdown(count):
    countdown_label.config(text=str(count) if count else "")
    window.update()


def show_qr(gallery_url, label_text="Scan to view & download your photos"):
    qr_img = make_qr_image(gallery_url).resize((320, 320))
    qr_photo = ImageTk.PhotoImage(qr_img)
    qr_label.config(image=qr_photo, text="")
    qr_label.image = qr_photo  # keep a reference so it isn't garbage collected
    url_label.config(text=gallery_url)
    scan_label.config(text=label_text)
    qr_frame.pack(pady=10)


def hide_qr():
    qr_frame.pack_forget()
    qr_label.config(image="", text="")
    url_label.config(text="")


def show_idle_qr():
    """Default 'waiting for a guest' screen: QR for the whole event
    gallery (every session so far), not any one guest's photos."""
    show_qr(PARTY_GALLERY_URL, "Scan to see everyone's photos from the party")


def show_collage(image_files):
    collage_img = make_collage(image_files, max_size=(screen_w - 100, screen_h - 340))
    collage_photo = ImageTk.PhotoImage(collage_img)
    collage_label.config(image=collage_photo)
    collage_label.image = collage_photo  # keep a reference so it isn't garbage collected
    collage_frame.pack(pady=10)


def hide_collage():
    collage_frame.pack_forget()
    collage_label.config(image="")


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


def show_session_qr_then_idle(gallery_url):
    hide_collage()
    show_qr(gallery_url)
    window.after(SESSION_QR_DISPLAY_MS, lambda: (hide_qr(), show_idle_qr()))


def back_to_idle_after_collage():
    hide_collage()
    show_idle_qr()


def on_take_photos():
    hide_qr()
    hide_collage()
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
        show_collage(image_files)
        button_take_photos.config(text="Uploading…")
        window.update()

        try:
            gallery_url = uploader.upload_session(image_files, session_id=session_id)
            window.after(COLLAGE_DISPLAY_MS, lambda: show_session_qr_then_idle(gallery_url))
        except Exception as upload_err:
            pending.add(session_id, uploader.event_slug, image_files)
            update_pending_label()
            messagebox.showwarning(
                "Saved, upload pending",
                "Photos are saved and will upload automatically once the "
                f"connection is back. ({upload_err})",
            )
            window.after(COLLAGE_DISPLAY_MS, back_to_idle_after_collage)
    except Exception as e:
        messagebox.showerror("Error", f"Something went wrong: {e}")
        show_idle_qr()
    finally:
        countdown_label.config(text="")
        button_take_photos.config(state=tk.NORMAL, text="Take Photos")


# ---- UI ----

window = tk.Tk()
window.title("Photo Booth")

# Kiosk mode: no window manager is running, so "-fullscreen" (an EWMH/WM
# hint) has nothing to act on it and the window shrinks to fit its content
# at the default 0,0 position. Size and place it to the actual screen
# directly instead, and center this content frame within that root.
screen_w = window.winfo_screenwidth()
screen_h = window.winfo_screenheight()
window.geometry(f"{screen_w}x{screen_h}+0+0")
window.bind("<Escape>", lambda e: window.destroy())

content = tk.Frame(window)
content.place(relx=0.5, rely=0.5, anchor=tk.CENTER)

label_welcome = tk.Label(content, text=EVENT_TITLE, font=("Helvetica", 22, "bold"))
label_welcome.pack(pady=(20, 5), fill=tk.BOTH, anchor=tk.CENTER)

if EVENT_HASHTAG:
    label_hashtag = tk.Label(content, text=EVENT_HASHTAG, font=("Helvetica", 14))
    label_hashtag.pack(pady=(0, 10), anchor=tk.CENTER)

image = Image.open("mtm.png")
photo = ImageTk.PhotoImage(image)
label_image = tk.Label(content, image=photo)
label_image.pack(pady=10)

countdown_label = tk.Label(content, text="", font=("Helvetica", 60, "bold"), fg="blue")
countdown_label.pack(pady=10)

button_take_photos = tk.Button(
    content,
    text="Take Photos",
    font=("Helvetica", 15, "bold"),
    command=on_take_photos,
    bg="blue",
    fg="white",
)
button_take_photos.pack(padx=40, pady=20, fill=tk.BOTH, anchor=tk.CENTER)

pending_label = tk.Label(content, text="", font=("Helvetica", 11), fg="darkorange")
pending_label.pack(pady=(0, 5))

collage_frame = tk.Frame(content)
collage_label = tk.Label(collage_frame)
collage_label.pack()

qr_frame = tk.Frame(content)
qr_label = tk.Label(qr_frame)
qr_label.pack()
url_label = tk.Label(qr_frame, font=("Helvetica", 11), fg="gray")
url_label.pack()
scan_label = tk.Label(qr_frame, text="Scan to view & download your photos", font=("Helvetica", 13, "bold"))
scan_label.pack(before=qr_label)

if capture_button:
    # gpiozero fires when_pressed on its own thread; Tkinter isn't
    # thread-safe, so hop back onto the main loop via window.after.
    # Guard against re-triggering mid-capture the same way the
    # on-screen button already does (it disables itself while busy).
    def _on_physical_button():
        if str(button_take_photos["state"]) == tk.NORMAL:
            on_take_photos()

    capture_button.when_pressed = lambda: window.after(0, _on_physical_button)

show_idle_qr()  # default "waiting for a guest" screen: whole-party gallery
update_pending_label()
window.after(2000, periodic_retry)  # give the app a moment to draw first

window.mainloop()
