"""Track photo sessions that failed to upload, so they can be retried.

Captured photos always land on local disk first (see camera.py); if the
S3 upload then fails (dead wifi, etc.), the session goes in this queue so
the app can retry it automatically later without losing the photos.

Safe to call from multiple threads (the UI thread adds failed sessions;
the background retry thread removes recovered ones).
"""

import json
import os
import threading

DEFAULT_PATH = "pics/pending_uploads.json"

_lock = threading.RLock()


def load(path=DEFAULT_PATH):
    with _lock:
        if not os.path.exists(path):
            return []
        with open(path) as f:
            return json.load(f)


def save(records, path=DEFAULT_PATH):
    with _lock:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w") as f:
            json.dump(records, f)


def add(session_id, event_slug, files, path=DEFAULT_PATH):
    with _lock:
        records = [r for r in load(path) if r["session_id"] != session_id]
        records.append({"session_id": session_id, "event_slug": event_slug, "files": files})
        save(records, path)


def remove(session_id, path=DEFAULT_PATH):
    with _lock:
        save([r for r in load(path) if r["session_id"] != session_id], path)
