(function () {
  const params = new URLSearchParams(window.location.search);
  const event = params.get("event");
  const session = params.get("session");

  const titleEl = document.getElementById("event-title");
  const subtitleEl = document.getElementById("event-subtitle");
  const statusEl = document.getElementById("status");
  const galleryEl = document.getElementById("gallery");
  const mineBannerEl = document.getElementById("mine-banner");
  const tapHintEl = document.getElementById("tap-hint");

  // Lightbox elements
  const lb = document.getElementById("lightbox");
  const lbImg = document.getElementById("lb-img");
  const lbCount = document.getElementById("lb-count");
  const lbHint = document.getElementById("lb-hint");
  const lbSave = document.getElementById("lb-save");
  const lbOpen = document.getElementById("lb-open");

  const isIOS =
    /iPad|iPhone|iPod/.test(navigator.userAgent) ||
    (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);
  const isTouch = window.matchMedia("(pointer: coarse)").matches;

  let photos = []; // [{key, filename, isMine}] in display order
  let index = 0;

  if (!event) {
    statusEl.textContent =
      "No event specified. Scan the QR code at the photo booth to open your event's gallery.";
    return;
  }

  fetch(`${event}/manifest.json`, { cache: "no-store" })
    .then((res) => {
      if (!res.ok) throw new Error("no manifest yet");
      return res.json();
    })
    .then((manifest) => {
      // The event name comes from the booth's config.ini via the manifest.
      const name = manifest.title || "Photo Booth Gallery";
      titleEl.textContent = name;
      document.title = name;
      renderGallery(manifest.photos || []);
    })
    .catch(() => {
      statusEl.textContent = "No photos yet for this event — check back after the next session!";
    });

  function renderGallery(photoKeys) {
    if (photoKeys.length === 0) {
      statusEl.textContent = "No photos yet for this event — check back after the next session!";
      return;
    }

    statusEl.hidden = true;
    tapHintEl.hidden = false;

    const newestFirst = [...photoKeys].reverse();
    let mineCount = 0;

    photos = newestFirst.map((key) => {
      const filename = key.split("/").pop();
      const isMine = Boolean(session) && filename.startsWith(`${session}-`);
      if (isMine) mineCount += 1;
      // Grid tiles load a small thumbnail; the booth uploads one alongside
      // each photo. Sessions taken before thumbnails existed have none, so
      // the tile falls back to the full photo (see the onerror below).
      return { key, thumb: key.replace("/photos/", "/thumbs/"), filename, isMine };
    });

    const count = photos.length;
    subtitleEl.textContent =
      count === 1 ? "1 photo from this event" : `${count} photos from this event, newest first`;

    photos.forEach((photo, i) => {
      const card = document.createElement("div");
      card.className = "photo-card" + (photo.isMine ? " mine" : "");

      // The whole thumbnail is the button that opens the full-screen view.
      const btn = document.createElement("button");
      btn.className = "photo-open";
      btn.setAttribute("aria-label", `View photo ${i + 1} of ${count} full screen`);
      btn.addEventListener("click", () => openLightbox(i));

      const img = document.createElement("img");
      img.src = photo.thumb;
      img.loading = "lazy";
      img.decoding = "async";
      img.alt = `Event photo ${i + 1}`;
      img.addEventListener(
        "error",
        () => {
          if (img.src !== photo.key) img.src = photo.key; // no thumbnail for this one
        },
        { once: true }
      );
      btn.appendChild(img);
      card.appendChild(btn);

      if (photo.isMine) {
        const tag = document.createElement("span");
        tag.className = "mine-tag";
        tag.textContent = "Yours";
        card.appendChild(tag);
      }

      const view = document.createElement("button");
      view.className = "card-action";
      view.textContent = "View & save";
      view.addEventListener("click", () => openLightbox(i));
      card.appendChild(view);

      galleryEl.appendChild(card);
    });

    if (mineCount > 0) mineBannerEl.hidden = false;
  }

  // ---- full-screen viewer ----

  function saveHint() {
    // navigator.share with files needs a secure context (HTTPS). This gallery
    // is served over plain HTTP from the S3 website endpoint, so on phones the
    // reliable path is the OS's own long-press save. If the site is ever put
    // behind HTTPS, the one-tap Save button below lights up automatically.
    if (isIOS) return "Touch and hold the photo above, then tap “Save to Photos”.";
    if (isTouch) return "Touch and hold the photo above, then tap “Download image”.";
    return "Right-click the photo to save it, or use “Open original” below.";
  }

  function canWebShare(file) {
    return (
      typeof navigator.canShare === "function" &&
      typeof navigator.share === "function" &&
      navigator.canShare({ files: [file] })
    );
  }

  function show(i) {
    index = (i + photos.length) % photos.length;
    const photo = photos[index];
    lbImg.src = photo.key;
    lbImg.alt = `Event photo ${index + 1} of ${photos.length}`;
    lbCount.textContent =
      `Photo ${index + 1} of ${photos.length}` + (photo.isMine ? " · Yours" : "");
    lbHint.textContent = saveHint();
    lbOpen.href = photo.key;
    lbOpen.setAttribute("download", photo.filename);
    lbSave.hidden = true;
    lbSave.disabled = false;
    lbSave.textContent = "Save photo";

    // Offer one-tap save only where the browser really supports sharing files.
    if (window.isSecureContext && typeof navigator.canShare === "function") {
      const probe = new File([new Blob([""], { type: "image/jpeg" })], photo.filename, {
        type: "image/jpeg",
      });
      if (canWebShare(probe)) lbSave.hidden = false;
    }
  }

  function openLightbox(i) {
    show(i);
    lb.hidden = false;
    document.body.classList.add("lb-open-body");
  }

  function closeLightbox() {
    lb.hidden = true;
    lbImg.removeAttribute("src");
    document.body.classList.remove("lb-open-body");
  }

  lbSave.addEventListener("click", async () => {
    const photo = photos[index];
    lbSave.disabled = true;
    lbSave.textContent = "Preparing…";
    try {
      const res = await fetch(photo.key, { cache: "force-cache" });
      const blob = await res.blob();
      const file = new File([blob], photo.filename, { type: blob.type || "image/jpeg" });
      if (canWebShare(file)) {
        await navigator.share({ files: [file] });
        lbSave.textContent = "Save photo";
      } else {
        lbHint.textContent = saveHint();
        lbSave.hidden = true;
      }
    } catch (err) {
      // A user cancelling the share sheet lands here too -- not an error worth showing.
      lbSave.textContent = "Save photo";
    } finally {
      lbSave.disabled = false;
    }
  });

  document.getElementById("lb-close").addEventListener("click", closeLightbox);
  document.getElementById("lb-prev").addEventListener("click", () => show(index - 1));
  document.getElementById("lb-next").addEventListener("click", () => show(index + 1));

  // Click the backdrop (but not the photo or controls) to dismiss.
  lb.addEventListener("click", (e) => {
    if (e.target === lb || e.target.classList.contains("lb-figure")) closeLightbox();
  });

  document.addEventListener("keydown", (e) => {
    if (lb.hidden) return;
    if (e.key === "Escape") closeLightbox();
    else if (e.key === "ArrowLeft") show(index - 1);
    else if (e.key === "ArrowRight") show(index + 1);
  });

  // Swipe between photos, without stealing the long-press-to-save gesture.
  let touchX = null;
  let touchY = null;
  let touchTime = 0;
  lb.addEventListener(
    "touchstart",
    (e) => {
      if (e.touches.length !== 1) return;
      touchX = e.touches[0].clientX;
      touchY = e.touches[0].clientY;
      touchTime = Date.now();
    },
    { passive: true }
  );
  lb.addEventListener(
    "touchend",
    (e) => {
      if (touchX === null) return;
      const dx = e.changedTouches[0].clientX - touchX;
      const dy = e.changedTouches[0].clientY - touchY;
      const quick = Date.now() - touchTime < 600; // a long press is a save, not a swipe
      if (quick && Math.abs(dx) > 50 && Math.abs(dx) > Math.abs(dy)) {
        show(dx < 0 ? index + 1 : index - 1);
      }
      touchX = touchY = null;
    },
    { passive: true }
  );
})();
