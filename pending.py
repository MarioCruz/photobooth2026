"""Track photo sessions that failed to upload, so they can be retried.

Captured photos always land on local disk first (see camera.py); every
session is queued here BEFORE its upload starts and removed only after
S3 confirms it, so a crash or power cut mid-upload can never lose track
of photos -- the retry loop finds them on the next pass or next boot.

Safe to call from multiple threads (the UI thread adds sessions; the
background retry/upload threads remove completed ones).
"""

import json
import os
import tempfile
import threading
import time

DEFAULT_PATH = "pics/pending_uploads.json"

_lock = threading.RLock()


def _valid_records(records):
    return isinstance(records, list) and all(
        isinstance(record, dict)
        and isinstance(record.get("session_id"), str)
        and isinstance(record.get("event_slug"), str)
        and isinstance(record.get("files"), list)
        and all(isinstance(path, str) for path in record["files"])
        for record in records
    )


def _quarantine(path):
    # Keep every bad copy for inspection rather than overwriting an earlier
    # recovery artifact from the same event.
    corrupt_path = f"{path}.corrupt-{time.time_ns()}"
    try:
        os.replace(path, corrupt_path)
    except OSError:
        pass


def load(path=DEFAULT_PATH):
    with _lock:
        try:
            with open(path) as f:
                records = json.load(f)
        except FileNotFoundError:
            return []
        except json.JSONDecodeError:
            # A power cut in an older non-atomic version, or a hand edit,
            # should not prevent the booth from starting.
            _quarantine(path)
            return []

        if not _valid_records(records):
            _quarantine(path)
            return []
        return records


def save(records, path=DEFAULT_PATH):
    with _lock:
        directory = os.path.dirname(path) or "."
        os.makedirs(directory, exist_ok=True)
        # Use a unique same-directory temporary file so os.replace() is
        # atomic and concurrent booth processes cannot share a temp name.
        fd, tmp = tempfile.mkstemp(
            prefix=f".{os.path.basename(path)}.", suffix=".tmp", dir=directory, text=True
        )
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(records, f)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, path)
        finally:
            try:
                os.unlink(tmp)
            except FileNotFoundError:
                pass


def add(session_id, event_slug, files, path=DEFAULT_PATH):
    with _lock:
        records = [r for r in load(path) if r["session_id"] != session_id]
        records.append({"session_id": session_id, "event_slug": event_slug, "files": files})
        save(records, path)


def remove(session_id, path=DEFAULT_PATH):
    with _lock:
        save([r for r in load(path) if r["session_id"] != session_id], path)
