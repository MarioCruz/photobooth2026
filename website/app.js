(function () {
  const params = new URLSearchParams(window.location.search);
  const event = params.get("event");
  const session = params.get("session");

  const titleEl = document.getElementById("event-title");
  const subtitleEl = document.getElementById("event-subtitle");
  const statusEl = document.getElementById("status");
  const galleryEl = document.getElementById("gallery");
  const mineBannerEl = document.getElementById("mine-banner");

  if (!event) {
    statusEl.textContent =
      "No event specified. Scan the QR code at the photo booth to open your event's gallery.";
    return;
  }

  subtitleEl.textContent = "Photos from this event, newest first.";

  fetch(`${event}/manifest.json`, { cache: "no-store" })
    .then((res) => {
      if (!res.ok) throw new Error("no manifest yet");
      return res.json();
    })
    .then((manifest) => {
      titleEl.textContent = manifest.title || "Photo Booth Gallery";
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

    const newestFirst = [...photoKeys].reverse();
    let mineCount = 0;

    newestFirst.forEach((key) => {
      const filename = key.split("/").pop();
      const isMine = Boolean(session) && filename.startsWith(`${session}-`);
      if (isMine) mineCount += 1;

      const card = document.createElement("div");
      card.className = "photo-card" + (isMine ? " mine" : "");

      const img = document.createElement("img");
      img.src = key;
      img.loading = "lazy";
      img.alt = "Event photo";
      card.appendChild(img);

      if (isMine) {
        const tag = document.createElement("span");
        tag.className = "mine-tag";
        tag.textContent = "Yours";
        card.appendChild(tag);
      }

      const link = document.createElement("a");
      link.className = "download";
      link.href = key;
      link.target = "_blank";
      link.rel = "noopener";
      link.download = filename;
      link.textContent = "Download";
      card.appendChild(link);

      galleryEl.appendChild(card);
    });

    if (mineCount > 0) {
      mineBannerEl.hidden = false;
    }
  }
})();
