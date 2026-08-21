# photobooth2026

Event photo booth for a Raspberry Pi + Pi Camera. Guests step up, hit
**Take Photos**, and get a QR code on screen that opens a web gallery
where they (and everyone else at the event) can view and download their
photos. No email, no Twitter/X — photos go straight to S3.

Successor to [Photobooth2023](https://github.com/MarioCruz/Photobooth2023)
(archived), which emailed photos instead.

## How it works

1. `Photobooth2.py` runs the Tkinter UI on the Pi and drives the camera
   (`camera.py`, using `picamera2`).
2. Captured photos are saved locally to `pics/<session-id>-<n>.jpg` first,
   then uploaded to S3 under `s3://<bucket>/<event-slug>/photos/`
   (`gallery.py`), and added to a shared `manifest.json` for that event.
3. A QR code (`qr.py`) encodes a link to the static gallery page
   (`website/`), e.g.
   `https://booth.mariothemaker.com/?event=<slug>&session=<id>`.
4. The gallery page fetches that event's `manifest.json` and renders every
   photo taken so far, newest first — the guest's own photos (matched by
   `session`) are tagged **Yours**. Tapping a photo opens it full screen,
   where it can be saved.

The booth screen shows the whole-event QR while it waits for the next
guest, then that guest's own QR after their photos upload.

Each event is just a folder (S3 key prefix) inside one bucket — no need to
create a new bucket per event, just change `[event] name` in `config.ini`.
The actual S3 path/URL uses a random, unguessable per-event `slug`
(auto-generated and saved into `config.ini` the first time an event runs),
not the human-readable name — that's what keeps one event's shared public
gallery from being browsable by anyone who wasn't at that event.

## Photos, thumbnails and the gallery

Photos are captured at the sensor's full 8MP (3280x2464). Uploading those
as gallery tiles would mean tens of megabytes before anything appeared on
a phone, so a ~35KB thumbnail is uploaded beside each photo
(`<slug>/thumbs/…`) and used for the grid; the full photo is fetched only
when someone opens it. Tiles fall back to the full photo if a thumbnail is
missing, and `deploy/backfill_thumbs.py` fills in events shot before
thumbnails existed.

Saving to a phone is deliberately explicit: on iOS a `download` link puts
the file in Files, not Photos, which is not what anyone wants from a photo
booth. Over HTTPS the viewer shows a one-tap **Save photo** button
(`navigator.share`, which opens the native share sheet). That API requires
a secure context, so over plain HTTP the button hides itself and the
viewer explains the press-and-hold gesture instead.

## If the upload fails

Photos always land on local disk in `pics/` before anything is uploaded.
Every session is journaled to `pics/pending_uploads.json` *before* its
upload starts and cleared only once S3 confirms it, so a crash or power
cut mid-upload can't orphan photos — the retry loop picks them up on the
next pass or the next boot. The queue is written atomically and validated
on load, so a half-written file can't stop the booth from starting.

If the upload fails (e.g. venue wifi drops), the guest sees "saved, upload
pending" rather than a dead-end error, and the app retries every 2 minutes
and again on startup — no one needs to touch the Pi. A slow upload never
holds the booth against the next guest: after a grace period it returns to
idle and finishes in the background. A label on screen shows how many
sessions are still waiting.

## One-time AWS setup

Requires the AWS CLI configured with a profile that can create S3/IAM
resources (this project used a profile named `PITA`).

```
./deploy/setup_aws.sh
```

This creates the S3 bucket (static website hosting + public-read objects
only, nothing listable/writable by the public), an IAM user scoped to just
that bucket, and an access key. It's idempotent — safe to re-run.

Copy the printed access key + website endpoint into `config.ini` (copy
`config.example.ini` to `config.ini` first — it's gitignored since it holds
live credentials).

Whenever `website/` changes, republish it with:

```
./deploy/deploy_site.sh
```

### HTTPS on a custom domain

```
./deploy/setup_https.sh
```

Requests an ACM certificate (DNS-validated in Route53) and puts a
CloudFront distribution in front of the bucket, so the gallery is served
from `https://booth.mariothemaker.com` instead of the plain-HTTP S3
website endpoint. Also idempotent. Beyond the nicer URL, HTTPS is what
enables the one-tap **Save photo** button on phones. Set
`[website] base_url` in `config.ini` to the HTTPS URL afterwards.

`deploy_site.sh` invalidates the CloudFront cache automatically, so site
changes show up immediately rather than hours later.

## Raspberry Pi setup

```
sudo apt-get install python3-tk python3-pil python3-pil.imagetk
pip install -r requirements.txt
```

`requirements.txt` includes `picamera2` (the modern libcamera-based camera
library — replaces the deprecated `picamera` this project used to use).

Fill in `config.ini` (event name/title, AWS bucket + region + credentials,
website base URL), then run:

```
python3 Photobooth2.py
```

If `picamera2` isn't available (e.g. developing off-Pi), `camera.py`
automatically falls back to generating placeholder photos, so the rest of
the pipeline (S3 upload, gallery, QR) can be built and tested without
hardware.

## Changing/adding an event

Edit `[event] name`/`title` in `config.ini`, blank out `slug` so a fresh
unguessable one is generated, and restart the app. New events get their
own manifest automatically on first upload. Local photos accumulate in
`pics/` as a backup — clear it between events.

![pi-camera-attached](https://user-images.githubusercontent.com/1426877/227970625-08ccf26c-f8ca-4326-8524-e4d1b1b046fe.jpg)
Photo from Raspberry Pi Foundation
