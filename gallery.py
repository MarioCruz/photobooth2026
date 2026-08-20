"""Upload photobooth sessions to S3 and hand back a shareable gallery URL.

Each event gets a random, unguessable slug that's used for its S3 prefix
and gallery URL -- separate from the human-readable name/title in
config.ini. This keeps events isolated: nobody can browse from one event's
link into another event's photos, since the slug can't be guessed and the
bucket has no public listing permission.
"""

import json
import secrets
import uuid

import boto3
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
        )

    def _manifest_key(self):
        return f"{self.event_slug}/manifest.json"

    def _load_manifest(self):
        try:
            resp = self.client.get_object(Bucket=self.bucket, Key=self._manifest_key())
            return json.loads(resp["Body"].read())
        except ClientError as e:
            if e.response["Error"]["Code"] in ("NoSuchKey", "404"):
                return {"title": self.event_title, "photos": []}
            raise

    def _save_manifest(self, manifest):
        self.client.put_object(
            Bucket=self.bucket,
            Key=self._manifest_key(),
            Body=json.dumps(manifest).encode("utf-8"),
            ContentType="application/json",
            CacheControl="no-cache",
        )

    def upload_session(self, image_files):
        """Upload a batch of local photo files, add them to the shared
        event manifest, and return the gallery URL for this session."""
        session_id = uuid.uuid4().hex[:8]
        manifest = self._load_manifest()
        manifest["title"] = self.event_title  # keep in sync with config.ini

        for i, path in enumerate(image_files):
            ext = path.split(".")[-1].lower()
            content_type = "image/jpeg" if ext == "jpg" else f"image/{ext}"
            key = f"{self.event_slug}/photos/{session_id}-{i}.{ext}"
            self.client.upload_file(
                path, self.bucket, key, ExtraArgs={"ContentType": content_type}
            )
            manifest["photos"].append(key)

        self._save_manifest(manifest)

        return f"{self.website_base_url}/?event={self.event_slug}&session={session_id}"
