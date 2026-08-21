#!/usr/bin/env python3
"""Generate gallery thumbnails for photos uploaded before thumbnails existed.

The booth writes a thumbnail alongside every photo it uploads (see
gallery.py). Events captured before that change have full 8MP photos only,
which makes their gallery page download tens of megabytes. This backfills
the missing thumbnails.

Usage:
    python3 deploy/backfill_thumbs.py            # every event in the bucket
    python3 deploy/backfill_thumbs.py <slug>     # just one event
    python3 deploy/backfill_thumbs.py --dry-run

Uses an admin AWS profile (default PITA), not the booth's upload-only user.
"""

import io
import os
import sys

import boto3
from PIL import Image

BUCKET = os.environ.get("BUCKET_NAME", "mariocruz-photobooth-gallery")
PROFILE = os.environ.get("PROFILE", "PITA")
REGION = os.environ.get("REGION", "us-east-1")
THUMB_BOX = (640, 640)
THUMB_QUALITY = 82


def main():
    args = [a for a in sys.argv[1:] if a != "--dry-run"]
    dry_run = "--dry-run" in sys.argv
    prefix = f"{args[0]}/photos/" if args else ""

    s3 = boto3.Session(profile_name=PROFILE, region_name=REGION).client("s3")
    paginator = s3.get_paginator("list_objects_v2")

    existing = set()
    for page in paginator.paginate(Bucket=BUCKET):
        for obj in page.get("Contents", []):
            if "/thumbs/" in obj["Key"]:
                existing.add(obj["Key"])

    made = skipped = failed = 0
    saved_bytes = 0
    for page in paginator.paginate(Bucket=BUCKET, Prefix=prefix):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            if "/photos/" not in key or not key.lower().endswith((".jpg", ".jpeg")):
                continue
            thumb_key = key.replace("/photos/", "/thumbs/", 1)
            if thumb_key in existing:
                skipped += 1
                continue
            try:
                body = s3.get_object(Bucket=BUCKET, Key=key)["Body"].read()
                img = Image.open(io.BytesIO(body))
                img.draft("RGB", THUMB_BOX)
                img.thumbnail(THUMB_BOX)
                buf = io.BytesIO()
                img.convert("RGB").save(buf, "JPEG", quality=THUMB_QUALITY, optimize=True)
                if not dry_run:
                    s3.put_object(
                        Bucket=BUCKET,
                        Key=thumb_key,
                        Body=buf.getvalue(),
                        ContentType="image/jpeg",
                        CacheControl="public, max-age=31536000",
                    )
                saved_bytes += len(body) - buf.tell()
                made += 1
                print(f"{'[dry-run] ' if dry_run else ''}{thumb_key}  "
                      f"{len(body)//1024}KB -> {buf.tell()//1024}KB")
            except Exception as e:
                failed += 1
                print(f"FAILED {key}: {e}")

    print(f"\n{made} thumbnail(s) {'would be ' if dry_run else ''}created, "
          f"{skipped} already present, {failed} failed")
    if made:
        print(f"gallery page download reduced by ~{saved_bytes / 1024 / 1024:.1f} MB")


if __name__ == "__main__":
    main()
