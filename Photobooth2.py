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
from gallery import GalleryUploader

RETRY_INTERVAL_MS = 2 * 60 * 1000  # re-attempt any queued uploads every 2 minutes
COLLAGE_DISPLAY_MS = 15 * 1000  # show the just-taken photos before the QR code
SESSION_QR_DISPLAY_MS = 15 * 1000  # then show that guest's QR before returning to idle
FAILURE_NOTICE_MS = 4 * 1000  # how long "saved, will upload later" stays up
UPLOAD_GRACE_MS = 20 * 1000  # extra wait for a slow upload after the collage, before freeing the booth
EVENT_POLL_MS = 100  # how often the Tk thread drains events from other threads

BG = "#111111"
FG = "#f5f5f5"
MUTED = "#9a9a9a"
WARN = "#ffb347"
ERR = "#ff6b6b"
ACCENT = "#1e6fff"

# Everything (config.ini, pics/, mtm.png) is addressed relative to this
# file's directory, so the app works no matter where it's launched from
# (systemd, .xinitrc, a shell in another cwd, ...).
os.chdir(os.path.dirname(os.path.abspath(__file__)))

config = configparser.ConfigParser()
config.read("config.ini")

EVENT_TITLE = config.get("event", "title")
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
HEADER_H = max(70, screen_h // 11)
FOOTER_H = max(80, screen_h // 10)
STAGE = (screen_w, screen_h - HEADER_H - FOOTER_H)
PREVIEW_SIZE = screens.fit(RESOLUTION, (STAGE[0] - 24, STAGE[1] - 24))

header = tk.Frame(window, bg=BG)
header.place(x=0, y=0, width=screen_w, height=HEADER_H)
tk.Label(header, text=EVENT_TITLE, font=("Helvetica", 28, "bold"), bg=BG, fg=FG).pack(pady=(10, 0))
if EVENT_HASHTAG:
    tk.Label(header, text=EVENT_HASHTAG, font=("Helvetica", 15), bg=BG, fg=MUTED).pack()

stage_photo = ImageTk.PhotoImage("RGB", STAGE)
stage_label = tk.Label(window, image=stage_photo, bg=BG, bd=0, highlightthickness=0)
stage_label.place(x=0, y=HEADER_H, width=STAGE[0], height=STAGE[1])

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

button_take_photos = tk.Button(
    footer,
    text="Take Photos",
    font=("Helvetica", 18, "bold"),
    command=lambda: start_session(),
    bg=ACCENT, fg="white", activebackground="#4b8bff", activeforeground="white",
    disabledforeground="#cfd8ff", bd=0, highlightthickness=0, padx=36, pady=10,
)
button_take_photos.grid(row=0, column=1)

if os.path.exists("mtm.png"):
    logo_img = Image.open("mtm.png")
    logo_img.thumbnail((FOOTER_H - 20, FOOTER_H - 20))
    logo_photo = ImageTk.PhotoImage(logo_img)
    tk.Label(footer, image=logo_photo, bg=BG).grid(row=0, column=2, sticky="e", padx=24)


def show(img):
    stage_photo.paste(img)


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
    state.update(phase="idle", session_id=None, files=[], upload=None, collage_done=False)
    show(screens.render_qr_screen(
        STAGE, PARTY_GALLERY_URL,
        "Press the button to take your photos!",
        "Scan to see everyone's photos from the party",
    ))
    button_take_photos.config(state=tk.NORMAL, text="Take Photos")
    refresh_pending_status()


# Live-view callbacks. capture_images() blocks the Tk thread for the whole
# countdown/shutter sequence, so each callback repaints via window.update(),
# which also lets Tk service events (button, Escape, timers) meanwhile.
live = {"frame": None, "count": None, "label": ""}


def _redraw_live():
    show(screens.render_preview(STAGE, live["frame"], live["count"], live["label"]))
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
        set_status(f"Camera problem: {e}", ERR)
        state["phase"] = "notice"
        schedule(FAILURE_NOTICE_MS, go_idle)
        return

    # Journal the complete capture immediately. Rendering or process failure
    # after this point cannot orphan the session from startup retries.
    pending.add(state["session_id"], uploader.event_slug, files)
    state.update(phase="collage", files=files)
    show(screens.render_collage(STAGE, files))
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
            STAGE, payload, "Your photos are ready!", "Scan to view & download your photos"
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
window.after(EVENT_POLL_MS, poll_events)
window.after(2000, periodic_retry)  # give the app a moment to draw first

window.mainloop()
