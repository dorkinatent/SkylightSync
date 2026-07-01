"""SQLite-backed dedup state for SkylightSync.

Replaces the legacy processed_photos.json file with a concurrency-safe SQLite
store. Two tables back two layers of dedup:

* processed_urls  -- lets the scraper skip a photo *before* downloading it,
                     keyed by the stable URL path (signed query string stripped).
* photos          -- content hashes, so the same image served under a new URL
                     is still recognised as already-seen.
"""

import json
import logging
import os
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from urllib.parse import urlparse

logger = logging.getLogger(__name__)


def normalize_url(url: str) -> str:
    """Stable per-photo key: the URL path with the signed query string dropped."""
    try:
        return urlparse(url).path.lstrip("/") or url
    except Exception:
        return url


class StateStore:
    def __init__(self, db_path: str = "data/skylight.db", legacy_data_dir: str | None = None) -> None:
        self.db_path = db_path
        self.legacy_data_dir = legacy_data_dir if legacy_data_dir is not None else os.path.dirname(db_path) or "."
        os.makedirs(os.path.dirname(self.db_path) or ".", exist_ok=True)
        self._initialize()

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _initialize(self) -> None:
        with self._connection() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS photos (
                    photo_hash TEXT PRIMARY KEY,
                    filename TEXT NOT NULL,
                    url TEXT,
                    timestamp TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS processed_urls (
                    normalized_url TEXT PRIMARY KEY,
                    photo_hash TEXT,
                    original_url TEXT,
                    processed_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS processed_guids (
                    photo_guid TEXT PRIMARY KEY,
                    photo_hash TEXT,
                    processed_at TEXT NOT NULL
                );
                """
            )
        self._migrate_legacy_json_if_needed()

    def _has_rows(self, table: str) -> bool:
        with self._connection() as conn:
            return conn.execute(f"SELECT 1 FROM {table} LIMIT 1").fetchone() is not None

    def _migrate_legacy_json_if_needed(self) -> None:
        """One-time import of an old processed_photos.json into SQLite."""
        if self._has_rows("photos"):
            return
        legacy_path = os.path.join(self.legacy_data_dir, "processed_photos.json")
        if not os.path.exists(legacy_path):
            return
        try:
            with open(legacy_path, encoding="utf-8") as f:
                photos = json.load(f)
            for photo_hash, payload in photos.items():
                self.add_photo(
                    photo_hash=photo_hash,
                    filename=payload.get("filename", f"{photo_hash}.jpg"),
                    url=payload.get("url"),
                    timestamp=payload.get("timestamp", datetime.now().strftime("%Y%m%d_%H%M%S")),
                )
                if payload.get("url"):
                    self.mark_url_processed(normalize_url(payload["url"]), photo_hash, payload["url"])
            logger.info("Migrated %d photos from %s into SQLite", len(photos), legacy_path)
        except Exception:
            logger.exception("Failed to migrate legacy %s", legacy_path)

    def is_url_processed(self, normalized_url: str) -> bool:
        with self._connection() as conn:
            row = conn.execute(
                "SELECT 1 FROM processed_urls WHERE normalized_url = ? LIMIT 1",
                (normalized_url,),
            ).fetchone()
        return row is not None

    def mark_url_processed(
        self, normalized_url: str, photo_hash: str | None, original_url: str | None
    ) -> None:
        with self._connection() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO processed_urls
                    (normalized_url, photo_hash, original_url, processed_at)
                VALUES (?, ?, ?, ?)
                """,
                (normalized_url, photo_hash, original_url, datetime.now().isoformat()),
            )

    def seen_guids(self) -> set[str]:
        """Every iCloud photoGuid already processed — the primary dedup key.

        The webstream API gives each photo a stable GUID, so we can skip
        already-seen photos before downloading (or even resolving) anything.
        """
        with self._connection() as conn:
            rows = conn.execute("SELECT photo_guid FROM processed_guids").fetchall()
        return {row["photo_guid"] for row in rows}

    def mark_guid_processed(self, photo_guid: str, photo_hash: str | None) -> None:
        """Record a GUID as handled so it is never re-fetched.

        Called even when the content hash was a duplicate, so a photo re-added
        to the album under a new GUID is still fetched exactly once."""
        with self._connection() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO processed_guids (photo_guid, photo_hash, processed_at)
                VALUES (?, ?, ?)
                """,
                (photo_guid, photo_hash, datetime.now().isoformat()),
            )

    def has_hash(self, photo_hash: str) -> bool:
        with self._connection() as conn:
            row = conn.execute(
                "SELECT 1 FROM photos WHERE photo_hash = ? LIMIT 1", (photo_hash,)
            ).fetchone()
        return row is not None

    def seen_hashes(self) -> set[str]:
        with self._connection() as conn:
            rows = conn.execute("SELECT photo_hash FROM photos").fetchall()
        return {row["photo_hash"] for row in rows}

    def commit_photo(
        self,
        *,
        photo_guid: str,
        photo_hash: str,
        filename: str,
        url: str | None,
        normalized_url: str,
        timestamp: str,
    ) -> None:
        """Record a photo as fully processed — call only after it has been sent.

        Writes all three dedup layers (content hash, URL, GUID) in a single
        transaction so a delivered photo is never left half-recorded, re-fetched,
        or re-sent."""
        now = datetime.now().isoformat()
        with self._connection() as conn:
            conn.execute(
                """
                INSERT INTO photos (photo_hash, filename, url, timestamp, created_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(photo_hash) DO UPDATE SET
                    filename = excluded.filename,
                    url = excluded.url,
                    timestamp = excluded.timestamp
                """,
                (photo_hash, filename, url, timestamp, now),
            )
            conn.execute(
                """
                INSERT OR REPLACE INTO processed_urls
                    (normalized_url, photo_hash, original_url, processed_at)
                VALUES (?, ?, ?, ?)
                """,
                (normalized_url, photo_hash, url, now),
            )
            conn.execute(
                """
                INSERT OR REPLACE INTO processed_guids (photo_guid, photo_hash, processed_at)
                VALUES (?, ?, ?)
                """,
                (photo_guid, photo_hash, now),
            )

    def add_photo(self, photo_hash: str, filename: str, url: str | None, timestamp: str) -> None:
        with self._connection() as conn:
            conn.execute(
                """
                INSERT INTO photos (photo_hash, filename, url, timestamp, created_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(photo_hash) DO UPDATE SET
                    filename = excluded.filename,
                    url = excluded.url,
                    timestamp = excluded.timestamp
                """,
                (photo_hash, filename, url, timestamp, datetime.now().isoformat()),
            )
