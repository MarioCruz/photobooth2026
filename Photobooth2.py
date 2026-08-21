#!/usr/bin/env python3
"""Event photo booth kiosk: live preview + countdown, a set of photos
shown as a collage, upload to S3, and a QR code so guests can grab them
from a web gallery. No email, no Twitter/X.

Screen flow:
  idle (QR for the whole event's gallery)
    -> press button: live preview with 3-2-1 countdown, x NUM_PHOTOS
    -> collage of the photos (upload happens in the background meanwhile)
    -> QR for this guest's photos
    -> back to idle
"""

import configparser
import os
import queue
import threading
import tkinter as tk
import uuid

from PIL import Image, ImageTk

import pending
import screens
from camera import capture_images
from collage import save_collage
from gallery import GalleryUploader

RETRY_INTERVAL_MS = 2 * 60 * 1000  # re-attempt any queued uploads every 2 minutes
COLLAGE_DISPLAY_MS = 15 * 1000  # show the just-taken photos before the QR code
SESSION_QR_DISPLAY_MS = 15 * 1000  # then show that guest's QR before returning to idle
FAILURE_NOTICE_MS = 4 * 1000  # how long "saved, will upload later" stays up
UPLOAD_GRACE_MS = 20 * 1000  # extra wait for a slow upload after the collage, before freeing the booth
EVENT_POLL_MS = 100  # how often the Tk thread drains events from other threads
COLLAGE_SAVE_SIZE = (2048, 1536)  # the 4-up grid, saved and uploaded as its own photo

# Near-black ground: the photos stay the brightest thing on screen and the
# white QR card reads at a distance. BG/FG/MUTED must match screens.py's RGB
# tuples exactly, or the rendered stage seams against the Tk header/footer.
BG = "#111111"
FG = "#f5f5f5"
MUTED = "#9a9a9a"
WARN = "#ffb347"
ERR = "#ff6b6b"
ACCENT = "#1e6fff"
ACCENT_ACTIVE = "#4b8bff"

# Everything (config.ini, pics/, mtm.png) is addressed relative to this
# file's directory, so the app works no matter where it's launched from
# (systemd, .xinitrc, a shell in another cwd, ...).
os.chdir(os.path.dirname(os.path.abspath(__file__)))

config = configparser.ConfigParser()
config.read("config.ini")

# [event] title is what the web gallery headlines itself with -- gallery.py
# reads it from config directly. The booth screen shows the instruction and
# the logo badge instead, so it doesn't repeat the event name here.
EVENT_HASHTAG = config.get("event", "hashtag", fallback="")

NUM_PHOTOS = max(1, config.getint("camera", "num_photos", fallback=4))
RESOLUTION = (
    config.getint("camera", "resolution_width", fallback=1920),
    config.getint("camera", "resolution_height", fallback=1080),
)
LOGO_PATH = config.get("camera", "logo_path", fallback="") or None
BUTTON_PIN = config.getint("camera", "button_pin", fallback=17)

uploader = GalleryUploader(config)
PARTY_GALLERY_URL = f"{uploader.website_base_url}/?event={uploader.event_slug}"

# Tk isn't thread-safe: background threads (uploads, the GPIO button)
# only ever talk to the UI by posting events here, drained on the Tk
# thread by poll_events().
events = queue.Queue()

# Sessions whose upload thread is running right now. Sessions are queued in
# pending.py *before* uploading (so a crash can't orphan them), which means
# the periodic retry would otherwise re-upload a session still in flight --
# doubling traffic on the very wifi that's already struggling.
_uploading = set()
_uploading_lock = threading.Lock()

try:
    from gpiozero import Button as GPIOButton

    # Physical trigger button: signal on BCM pin BUTTON_PIN (default
    # GPIO17 / physical pin 11), other leg to any GND (e.g. physical
    # pin 9). Internal pull-up means a plain push-to-make button needs
    # no external resistor. Falls back to on-screen-only if no button
    # is wired (e.g. developing off-Pi).
    capture_button = GPIOButton(BUTTON_PIN, bounce_time=0.2)
    capture_button.when_pressed = lambda: events.put(("button",))
except Exception:
    capture_button = None


# ---- window & layout ----

window = tk.Tk()
window.title("Photo Booth")
window.configure(bg=BG)
# Kiosk mode: no window manager is running, so "-fullscreen" (a WM hint)
# has nothing to act on it. Size and place the window to the screen directly.
screen_w = window.winfo_screenwidth()
screen_h = window.winfo_screenheight()
window.geometry(f"{screen_w}x{screen_h}+0+0")
window.bind("<Escape>", lambda e: window.destroy())

# Three fixed bands placed at absolute coordinates -- header (event title),
# stage (everything visual, rendered as one image by screens.py), footer
# (status, button, logo) -- so no screen state can push another off the display.
# The header carries the instruction, in the largest type on the screen --
# the branding is already on the logo badge in the footer, so repeating the
# event title up here just spent the most legible row on saying it twice.
HEADER_H = max(110, screen_h // 7)
# Tall enough for the button, the big arrow pointing down at the real one,
# and a logo badge with some presence.
FOOTER_H = max(160, screen_h // 5)
STAGE = (screen_w, screen_h - HEADER_H - FOOTER_H)
# While posing, the live view takes the whole screen rather than the middle
# band -- a 4:3 frame inside a 16:9 stage is height-limited, so giving it the
# header and footer rows back makes it noticeably bigger to frame yourself in.
PREVIEW_STAGE = (screen_w, screen_h)
PREVIEW_SIZE = screens.fit(RESOLUTION, (PREVIEW_STAGE[0] - 16, PREVIEW_STAGE[1] - 16))

# Raise the QR code off centre so it sits at a comfortable height to scan.
# Derived from the display's reported physical size rather than hardcoded, so
# it stays 0.6" on whatever screen the booth is plugged into.
_screen_mm_h = window.winfo_screenmmheight() or 249
QR_LIFT_PX = int(0.60 * (screen_h / (_screen_mm_h / 25.4)))

header = tk.Frame(window, bg=BG)
header.place(x=0, y=0, width=screen_w, height=HEADER_H)
tk.Label(
    header, text=f"Press the button for {NUM_PHOTOS} photos",
    font=("Helvetica", 44, "bold"), bg=BG, fg=FG,
).pack(pady=(14, 0))
tk.Label(
    header, text=f"Look at the camera — {NUM_PHOTOS} shots, 3-2-1 each",
    font=("Helvetica", 18), bg=BG, fg=MUTED,
).pack(pady=(2, 0))

stage_photo = ImageTk.PhotoImage("RGB", STAGE)
stage_label = tk.Label(window, image=stage_photo, bg=BG, bd=0, highlightthickness=0)
stage_label.place(x=0, y=HEADER_H, width=STAGE[0], height=STAGE[1])

# Full-screen overlay used only while posing (see PREVIEW_STAGE above).
preview_photo = ImageTk.PhotoImage("RGB", PREVIEW_STAGE)
preview_label = tk.Label(window, image=preview_photo, bg=BG, bd=0, highlightthickness=0)

footer = tk.Frame(window, bg=BG)
footer.place(x=0, y=HEADER_H + STAGE[1], width=screen_w, height=FOOTER_H)
footer.columnconfigure(0, weight=1, uniform="side")
footer.columnconfigure(1, weight=0)
footer.columnconfigure(2, weight=1, uniform="side")
footer.rowconfigure(0, weight=1)

status_label = tk.Label(
    footer, text="", font=("Helvetica", 15), bg=BG, fg=WARN,
    anchor="w", justify="left", wraplength=screen_w // 2 - 80,
)
status_label.grid(row=0, column=0, sticky="w", padx=24)

# Button sits high in the footer with an arrow beneath it, pointing down at
# the physical button on the booth so guests know what to actually press.
button_column = tk.Frame(footer, bg=BG)
button_column.grid(row=0, column=1, sticky="n", pady=(4, 0))

button_take_photos = tk.Button(
    button_column,
    text="Take Photos",
    font=("Helvetica", 18, "bold"),
    command=lambda: start_session(),
    bg=ACCENT, fg="white", activebackground=ACCENT_ACTIVE, activeforeground="white",
    disabledforeground="#cfd8ff", bd=0, highlightthickness=0, padx=36, pady=10,
)
button_take_photos.pack()

arrow_label = tk.Label(
    button_column, text="▼", font=("Helvetica", 60, "bold"), bg=BG, fg=ACCENT
)
arrow_label.pack(pady=(14, 0))  # sits clear of the button, closer to the real one

ARROW_BLINK_MS = 600
_arrow_on = [True]


def blink_arrow():
    """Pulse the arrow so it draws the eye to the physical button. Kept off
    state['timers'] so cancel_timers() during a session can't stop it."""
    _arrow_on[0] = not _arrow_on[0]
    arrow_label.config(fg=ACCENT if _arrow_on[0] else BG)
    window.after(ARROW_BLINK_MS, blink_arrow)

if os.path.exists("mtm.png"):
    # On a white disc so the black mark stays legible against the dark ground.
    logo_photo = ImageTk.PhotoImage(screens.logo_badge("mtm.png", FOOTER_H - 16))
    tk.Label(footer, image=logo_photo, bg=BG, bd=0).grid(row=0, column=2, sticky="e", padx=28)


def show(img):
    stage_photo.paste(img)


def show_fullscreen_preview(img):
    preview_photo.paste(img)
    if not preview_label.winfo_ismapped():
        preview_label.place(x=0, y=0, width=PREVIEW_STAGE[0], height=PREVIEW_STAGE[1])
        preview_label.lift()


def hide_fullscreen_preview():
    preview_label.place_forget()


def set_status(text, color=WARN):
    status_label.config(text=text, fg=color)


# ---- session state machine ----

state = {
    "phase": "idle",  # idle | capturing | collage | qr | notice
    "timers": [],
    "session_id": None,
    "files": [],
    "upload": None,  # None while in flight, else ("ok", url) or ("fail", error)
    "collage_done": False,
}


def schedule(ms, fn):
    state["timers"].append(window.after(ms, fn))


def cancel_timers():
    for t in state["timers"]:
        window.after_cancel(t)
    state["timers"].clear()


def refresh_pending_status():
    n = len(pending.load())
    set_status(f"{n} session(s) waiting to upload…" if n else "")


def go_idle():
    cancel_timers()
    hide_fullscreen_preview()  # belt and braces if a session ended unusually
    state.update(phase="idle", session_id=None, files=[], upload=None, collage_done=False)
    show(screens.render_qr_screen(
        STAGE, PARTY_GALLERY_URL,
        "Scan to see everyone's photos from the party",
        EVENT_HASHTAG,  # instruction moved to the header, so this slot is free
        lift=QR_LIFT_PX,
    ))
    button_take_photos.config(state=tk.NORMAL, text="Take Photos")
    refresh_pending_status()


# Live-view callbacks. capture_images() blocks the Tk thread for the whole
# countdown/shutter sequence, so each callback repaints via window.update(),
# which also lets Tk service events (button, Escape, timers) meanwhile.
live = {"frame": None, "count": None, "label": ""}


def _redraw_live():
    show_fullscreen_preview(
        screens.render_preview(PREVIEW_STAGE, live["frame"], live["count"], live["label"])
    )
    window.update()


def on_shot(i, n):
    live["label"] = f"Photo {i} of {n}"


def on_countdown(count):
    live["count"] = count
    _redraw_live()


def on_preview(frame):
    live["frame"] = frame
    _redraw_live()


def start_session():
    if state["phase"] not in ("idle", "qr", "notice"):
        return  # ignore presses while capturing/uploading
    cancel_timers()
    state.update(
        phase="capturing", session_id=uuid.uuid4().hex[:8], files=[], upload=None, collage_done=False
    )
    button_take_photos.config(state=tk.DISABLED, text="Taking photos…")
    set_status("")
    live.update(frame=None, count=None, label="Get ready!")
    _redraw_live()

    try:
        files = capture_images(
            NUM_PHOTOS,
            resolution=RESOLUTION,
            on_countdown=on_countdown,
            on_preview=on_preview,
            on_shot=on_shot,
            logo_path=LOGO_PATH,
            session_id=state["session_id"],
            preview_size=PREVIEW_SIZE,
        )
    except Exception as e:
        hide_fullscreen_preview()
        set_status(f"Camera problem: {e}", ERR)
        state["phase"] = "notice"
        schedule(FAILURE_NOTICE_MS, go_idle)
        return

    hide_fullscreen_preview()

    # Save the 4-up grid as a photo in its own right, so guests can share the
    # whole strip as one image. It rides along as the session's last photo, so
    # it uploads, retries and appears in the gallery like any other. Failing to
    # build it must not cost anyone their actual photos.
    try:
        files = files + [save_collage(files, f"pics/{state['session_id']}-collage.jpg",
                                      size=COLLAGE_SAVE_SIZE)]
    except Exception as e:
        print(f"collage save failed, continuing with individual photos: {e}")

    # Journal the complete capture immediately. Rendering or process failure
    # after this point cannot orphan the session from startup retries.
    pending.add(state["session_id"], uploader.event_slug, files)
    state.update(phase="collage", files=files)
    show(screens.render_collage(STAGE, files[:-1] if len(files) > 1 else files))
    button_take_photos.config(text="Uploading…")
    set_status("Uploading your photos…")
    with _uploading_lock:
        _uploading.add(state["session_id"])
    threading.Thread(target=_upload_worker, args=(state["session_id"], files), daemon=True).start()
    schedule(COLLAGE_DISPLAY_MS, _collage_done)


def _upload_worker(session_id, files):
    try:
        url = uploader.upload_session(files, session_id=session_id)
        events.put(("upload", session_id, files, "ok", url))
    except Exception as e:
        events.put(("upload", session_id, files, "fail", e))
    finally:
        with _uploading_lock:
            _uploading.discard(session_id)


def _collage_done():
    state["collage_done"] = True
    if state["phase"] == "collage" and state["upload"] is None:
        # Upload still in flight: give it a bounded grace period rather than
        # holding the booth hostage to dead venue wifi. The session is
        # already queued, so the background retry finishes the job if the
        # upload can't.
        schedule(UPLOAD_GRACE_MS, _upload_grace_expired)
    _advance_after_collage()


def _upload_grace_expired():
    if state["phase"] != "collage" or state["upload"] is not None:
        return  # the upload resolved in time; this timer is stale
    state["phase"] = "notice"
    button_take_photos.config(state=tk.NORMAL, text="Take Photos")
    set_status(
        "Photos saved! They'll finish uploading in the background — "
        "scan the party code later to find them."
    )
    schedule(FAILURE_NOTICE_MS, go_idle)


def _advance_after_collage():
    """Move on from the collage once BOTH its display time is up and the
    upload has finished, one way or the other."""
    if state["phase"] != "collage" or not state["collage_done"] or state["upload"] is None:
        return
    outcome, payload = state["upload"]
    if outcome == "ok":
        state["phase"] = "qr"
        show(screens.render_qr_screen(
            STAGE, payload, "Your photos are ready!",
            "Scan to view & download your photos", lift=QR_LIFT_PX,
        ))
        set_status("")
        # The next guest can start while this QR is up.
        button_take_photos.config(state=tk.NORMAL, text="Take Photos")
        schedule(SESSION_QR_DISPLAY_MS, go_idle)
    else:
        # Already in the pending queue (sessions enqueue before uploading);
        # the periodic retry finishes the job once the wifi is back.
        state["phase"] = "notice"
        button_take_photos.config(state=tk.NORMAL, text="Take Photos")
        set_status(
            "Saved! Your photos will upload automatically once the wifi is back. "
            f"({str(payload)[:80]})"
        )
        schedule(FAILURE_NOTICE_MS, go_idle)


def poll_events():
    """Drain events posted by background threads, on the Tk thread."""
    while True:
        try:
            ev = events.get_nowait()
        except queue.Empty:
            break
        kind = ev[0]
        if kind == "button":
            start_session()
        elif kind == "upload":
            _, session_id, files, outcome, payload = ev
            if outcome == "ok":
                # Confirmed in S3 -- clear it from the crash-safe queue.
                # (On failure it simply stays queued for the retry loop.)
                pending.remove(session_id)
            if session_id == state["session_id"]:
                state["upload"] = (outcome, payload)
                _advance_after_collage()
            elif state["phase"] == "idle":
                refresh_pending_status()
        elif kind == "retry_done":
            if state["phase"] == "idle":
                refresh_pending_status()
    window.after(EVENT_POLL_MS, poll_events)


# ---- upload retry queue (background thread) ----

def retry_pending_uploads():
    """Retry any sessions that failed to upload earlier. Photos are never
    lost on a failed upload -- they stay in pics/ and queued in
    pics/pending_uploads.json until this succeeds."""
    for record in pending.load():
        with _uploading_lock:
            if record["session_id"] in _uploading:
                continue  # a live upload owns this session; don't send it twice
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
    events.put(("retry_done",))


_retry_thread = [None]


def periodic_retry():
    t = _retry_thread[0]
    if not (t and t.is_alive()):
        t = threading.Thread(target=retry_pending_uploads, daemon=True)
        _retry_thread[0] = t
        t.start()
    window.after(RETRY_INTERVAL_MS, periodic_retry)


go_idle()
blink_arrow()
window.after(EVENT_POLL_MS, poll_events)
window.after(2000, periodic_retry)  # give the app a moment to draw first

window.mainloop()
