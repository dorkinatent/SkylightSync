"""Fetch photos from a public iCloud shared album via its JSON web-stream API.

iCloud shared albums expose an undocumented but stable JSON API instead of
needing a headless browser to walk the photo carousel:

* POST .../sharedstreams/webstream    -> every photo with a stable ``photoGuid``,
                                          derivative sizes, and metadata.
* POST .../sharedstreams/webasseturls -> short-lived signed download URLs,
                                          keyed by each derivative's checksum.

The first request to the base host answers 330 with an ``X-Apple-MMe-Host``
pointing at the album's real partition; we cache that host and re-issue there.
"""

import hashlib
import json
import logging
import os
from datetime import datetime

import requests
from tenacity import retry, stop_after_attempt, wait_exponential

from state_store import StateStore, normalize_url

logger = logging.getLogger(__name__)

# Any partition host works for the initial call; it 330-redirects to the right one.
BASE_HOST = "p01-sharedstreams.icloud.com"

# The endpoints expect a raw JSON body (text/plain) with a browser-like origin.
_HEADERS = {
    "Content-Type": "text/plain",
    "Origin": "https://www.icloud.com",
    "Referer": "https://www.icloud.com/",
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
        "(KHTML, like Gecko) Version/17.0 Safari/605.1.15"
    ),
}

# webasseturls resolves every derivative of the GUIDs passed in one call; chunk
# so a large album doesn't produce an oversized request.
_GUID_CHUNK = 25


class ICloudPhotoScraper:
    def __init__(
        self,
        album_url: str,
        download_dir: str = "downloads",
        data_dir: str = "data",
        state_store: StateStore | None = None,
    ) -> None:
        self.album_url = album_url
        self.token = self._parse_token(album_url)
        self.download_dir = download_dir
        self.data_dir = data_dir

        os.makedirs(self.data_dir, exist_ok=True)
        os.makedirs(self.download_dir, exist_ok=True)

        self.state_store = state_store or StateStore(os.path.join(data_dir, "skylight.db"))

        self.session = requests.Session()
        self.session.headers.update(_HEADERS)
        self._host: str | None = None

    @staticmethod
    def _parse_token(album_url: str) -> str:
        """Extract the album token, e.g. ``B2U5Uzl7VCG6vv`` from
        ``https://www.icloud.com/sharedalbum/#B2U5Uzl7VCG6vv``."""
        if "#" in album_url:
            token = album_url.split("#", 1)[1]
        else:
            token = album_url.rstrip("/").rsplit("/", 1)[-1]
        return token.strip("/ ")

    def normalize_url(self, url: str) -> str:
        return normalize_url(url)

    def get_photo_hash(self, photo_data: bytes) -> str:
        return hashlib.md5(photo_data).hexdigest()

    # -- iCloud web-stream API -------------------------------------------------

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10), reraise=True)
    def _post(self, endpoint: str, body: dict) -> dict:
        host = self._host or BASE_HOST
        url = f"https://{host}/{self.token}/sharedstreams/{endpoint}"
        resp = self.session.post(url, data=json.dumps(body), timeout=45)
        # 330 == "wrong partition, use this host"; cache it and retry once.
        if resp.status_code == 330:
            self._host = resp.json()["X-Apple-MMe-Host"]
            url = f"https://{self._host}/{self.token}/sharedstreams/{endpoint}"
            resp = self.session.post(url, data=json.dumps(body), timeout=45)
        resp.raise_for_status()
        return resp.json()

    def _fetch_stream(self) -> list[dict]:
        return self._post("webstream", {"streamCtag": None}).get("photos", [])

    def _resolve_asset_urls(self, guids: list[str]) -> dict[str, str]:
        """Map each derivative checksum -> a signed download URL."""
        items: dict[str, dict] = {}
        locations: dict[str, dict] = {}
        for i in range(0, len(guids), _GUID_CHUNK):
            data = self._post("webasseturls", {"photoGuids": guids[i : i + _GUID_CHUNK]})
            items.update(data.get("items", {}))
            locations.update(data.get("locations", {}))

        urls: dict[str, str] = {}
        for checksum, item in items.items():
            loc = item.get("url_location")
            hosts = locations.get(loc, {}).get("hosts", [loc])
            scheme = locations.get(loc, {}).get("scheme", "https")
            urls[checksum] = f"{scheme}://{hosts[0]}{item.get('url_path', '')}"
        return urls

    @staticmethod
    def _best_derivative(photo: dict) -> dict | None:
        """The highest-quality derivative (largest file) for a photo record."""
        derivatives = photo.get("derivatives") or {}
        usable = [d for d in derivatives.values() if d.get("checksum")]
        if not usable:
            return None
        return max(usable, key=lambda d: int(d.get("fileSize") or 0))

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10), reraise=True)
    def _download(self, url: str) -> bytes:
        resp = requests.get(url, timeout=30)
        resp.raise_for_status()
        return resp.content

    @staticmethod
    def _filename(photo: dict, url: str, idx: int) -> str:
        base = url.split("?", 1)[0].rsplit("/", 1)[-1] or "photo.jpg"
        guid = (photo.get("photoGuid") or f"idx{idx}")[:8]
        return f"{guid}_{base}"

    # -- public API ------------------------------------------------------------

    @staticmethod
    def _is_video(photo: dict) -> bool:
        return photo.get("mediaAssetType") == "video"

    def scrape_photos(self) -> list[dict]:
        """Download new (non-video) photos and return uncommitted descriptors.

        State is intentionally NOT written for successfully downloaded photos:
        the caller commits each one (via ``state_store.commit_photo``) only after
        it has been emailed, so a failed send never orphans a photo as "seen but
        never delivered". Structurally unsupported entries (no derivative / no
        asset URL) are marked here, since they can never be delivered.

        Each returned item is a dict with keys: path, filename, guid, hash, url,
        normalized, timestamp, size.
        """
        try:
            photos = self._fetch_stream()
        except Exception as e:
            logger.error("Failed to fetch album web-stream: %s", e)
            return []

        logger.info("Album web-stream returned %d photo(s)", len(photos))

        seen_guids = self.state_store.seen_guids()
        unseen = [p for p in photos if p.get("photoGuid") and p["photoGuid"] not in seen_guids]

        # Photos-only: skip videos. They are left unmarked (not recorded as
        # processed) so they're simply ignored each run.
        videos = sum(1 for p in unseen if self._is_video(p))
        if videos:
            logger.info("Skipping %d video(s) (photos-only mode)", videos)
        # Oldest first, so batched emails arrive in chronological order.
        new = sorted(
            (p for p in unseen if not self._is_video(p)),
            key=lambda p: p.get("dateCreated", ""),
        )
        if not new:
            logger.info("No new photos to send")
            return []
        logger.info("%d new photo(s) after GUID dedup", len(new))

        # Choose the best derivative per photo, then resolve all URLs in bulk.
        # A photo with no usable derivative is structurally unsupported (not a
        # transient failure), so record its GUID now to avoid reprocessing it
        # on every future sync.
        chosen: dict[str, tuple[str, dict]] = {}
        for p in new:
            deriv = self._best_derivative(p)
            if deriv:
                chosen[p["photoGuid"]] = (deriv["checksum"], p)
            else:
                logger.warning("No usable derivative for photo %s", p.get("photoGuid"))
                self.state_store.mark_guid_processed(p["photoGuid"], None)

        asset_urls = self._resolve_asset_urls(list(chosen.keys()))

        seen_hashes = self.state_store.seen_hashes()
        pending: list[dict] = []

        for idx, (guid, (checksum, photo)) in enumerate(chosen.items()):
            url = asset_urls.get(checksum)
            if not url:
                # webasseturls returned no URL for this derivative in an
                # otherwise-successful response: treat as permanent and record
                # the GUID so it isn't retried forever. (A failed webasseturls
                # POST raises earlier and aborts the whole run, so nothing is
                # marked in that transient case.)
                logger.warning("No asset URL resolved for photo %s", guid)
                self.state_store.mark_guid_processed(guid, None)
                continue

            try:
                data = self._download(url)
            except Exception as e:
                # Transient: leave unmarked so it retries on the next run.
                logger.error("Error downloading photo %s: %s", guid, e)
                continue

            photo_hash = self.get_photo_hash(data)
            timestamp = photo.get("dateCreated") or datetime.now().strftime("%Y%m%d_%H%M%S")

            # Content-level dedup: same image re-added under a new GUID/URL.
            # This is a confirmed duplicate (not pending a send), so commit the
            # GUID immediately and don't queue it for email.
            if photo_hash in seen_hashes:
                logger.debug("Photo %s already seen by content hash, skipping send", guid)
                self.state_store.mark_guid_processed(guid, photo_hash)
                continue

            filename = self._filename(photo, url, idx)
            filepath = os.path.join(self.download_dir, filename)
            with open(filepath, "wb") as f:
                f.write(data)
            seen_hashes.add(photo_hash)
            pending.append(
                {
                    "path": filepath,
                    "filename": filename,
                    "guid": guid,
                    "hash": photo_hash,
                    "url": url,
                    "normalized": self.normalize_url(url),
                    "timestamp": timestamp,
                    "size": len(data),
                }
            )
            logger.info("Downloaded new photo: %s (%d bytes)", filename, len(data))

        logger.info("%d photo(s) downloaded and ready to send", len(pending))
        return pending
