"""Upload photobooth sessions to S3 and hand back a shareable gallery URL.

Each event gets a random, unguessable slug that's used for its S3 prefix
and gallery URL -- separate from the human-readable name/title in
config.ini. This keeps events isolated: nobody can browse from one event's
link into another event's photos, since the slug can't be guessed and the
bucket has no public listing permission.
"""

import json
import os
import secrets
import threading
import uuid

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError


def get_or_create_event_slug(config, config_path="config.ini"):
    """Return this event's slug, generating and persisting a new one to
    config.ini the first time (or whenever the slug field is blank, e.g.
    after switching to a new event name)."""
    slug = config.get("event", "slug", fallback="").strip()
    if slug:
        return slug

    slug = secrets.token_hex(6)  # 12 hex chars, unguessable
    config.set("event", "slug", slug)
    with open(config_path, "w") as f:
        config.write(f)
    return slug


class GalleryUploader:
    def __init__(self, config, config_path="config.ini"):
        aws_cfg = config["aws"]
        self.bucket = aws_cfg["bucket"]
        self.event_slug = get_or_create_event_slug(config, config_path)
        self.event_title = config.get("event", "title", fallback=config["event"]["name"])
        self.website_base_url = config["website"]["base_url"].rstrip("/")
        self.client = boto3.client(
            "s3",
            region_name=aws_cfg["region"],
            aws_access_key_id=aws_cfg["access_key_id"],
            aws_secret_access_key=aws_cfg["secret_access_key"],
            # Fail fast instead of hanging the UI on dead/flaky venue wifi.
            config=Config(connect_timeout=5, read_timeout=15, retries={"max_attempts": 2}),
        )
        # Uploads run on background threads (a guest's session and the
        # retry queue can overlap); serialize them so the shared per-event
        # manifest is never read-modify-written concurrently.
        self._lock = threading.Lock()

    def _manifest_key(self, event_slug):
        return f"{event_slug}/manifest.json"

    def _load_manifest(self, event_slug):
        try:
            resp = self.client.get_object(Bucket=self.bucket, Key=self._manifest_key(event_slug))
            return json.loads(resp["Body"].read())
        except ClientError as e:
            if e.response["Error"]["Code"] in ("NoSuchKey", "404"):
                return {"title": self.event_title, "photos": []}
            raise

    def _save_manifest(self, event_slug, manifest):
        self.client.put_object(
            Bucket=self.bucket,
            Key=self._manifest_key(event_slug),
            Body=json.dumps(manifest).encode("utf-8"),
            ContentType="application/json",
            CacheControl="no-cache",
        )

    def upload_session(self, image_files, session_id=None, event_slug=None):
        """Upload a batch of local photo files, add them to the shared
        event manifest, and return the gallery URL for this session.

        session_id/event_slug can be passed explicitly to retry a session
        that failed earlier (see pending.py) so it lands in the same event
        gallery it was originally captured for, even if config.ini has
        since moved on to a different event.
        """
        session_id = session_id or uuid.uuid4().hex[:8]
        event_slug = event_slug or self.event_slug
        with self._lock:
            manifest = self._load_manifest(event_slug)
            if event_slug == self.event_slug:
                manifest["title"] = self.event_title  # keep in sync with config.ini

            # Normalize any duplicates left by an interrupted older upload,
            # then use a set so foreground/retry races stay idempotent.
            photos = list(dict.fromkeys(manifest.setdefault("photos", [])))
            manifest["photos"] = photos
            known_photos = set(photos)

            for i, path in enumerate(image_files):
                ext = path.split(".")[-1].lower()
                content_type = "image/jpeg" if ext == "jpg" else f"image/{ext}"
                # Derive the key from the local filename (camera.py names
                # them <session_id>-<n>.jpg) rather than the loop index, so a
                # retry that can only find some of the photos still uploads
                # each under its original number instead of renumbering them
                # and overwriting a different shot.
                stem = os.path.splitext(os.path.basename(path))[0]
                if not stem.startswith(f"{session_id}-"):
                    stem = f"{session_id}-{i}"
                key = f"{event_slug}/photos/{stem}.{ext}"
                self.client.upload_file(
                    path, self.bucket, key, ExtraArgs={"ContentType": content_type}
                )
                # Re-uploading the same stable object key is harmless, but
                # each photo must appear in the event manifest only once.
                if key not in known_photos:
                    photos.append(key)
                    known_photos.add(key)

            self._save_manifest(event_slug, manifest)

        return f"{self.website_base_url}/?event={event_slug}&session={session_id}"
