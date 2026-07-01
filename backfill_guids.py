#!/usr/bin/env python3
"""One-off backfill: stamp every photoGuid currently in the album as processed.

Context: the switch to the iCloud web-stream API introduced GUID-based dedup
(the ``processed_guids`` table), which starts empty. Without this backfill the
first sync would treat all ~4k album photos as new and re-download every one
just to hash-confirm they're already-emailed duplicates.

Everything currently in the album has already been emailed (the photos table
holds more processed rows than the album has photos), so we can safely mark all
present GUIDs as processed -- without downloading anything. Genuinely new photos
added *after* this run get fresh GUIDs and email normally.

Idempotent: re-running only stamps GUIDs not already recorded.

Usage:
  python backfill_guids.py --dry-run   # show what would be stamped, write nothing
  python backfill_guids.py             # stamp all current album GUIDs
"""

import argparse
import logging
import os
import sys

from dotenv import load_dotenv

from icloud_scraper import ICloudPhotoScraper
from state_store import StateStore

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("backfill-guids")

DB_PATH = "data/skylight.db"


def main() -> None:
    ap = argparse.ArgumentParser(description="Backfill current album GUIDs as processed")
    ap.add_argument("--dry-run", action="store_true", help="report only; write nothing")
    args = ap.parse_args()

    load_dotenv()
    album = os.getenv("ICLOUD_ALBUM_URL")
    if not album:
        logger.error("ICLOUD_ALBUM_URL is not set")
        sys.exit(1)

    store = StateStore(DB_PATH)
    scraper = ICloudPhotoScraper(album, state_store=store)

    photos = scraper._fetch_stream()
    guids = [p["photoGuid"] for p in photos if p.get("photoGuid")]
    already = store.seen_guids()
    new = [g for g in guids if g not in already]

    logger.info("Album photos: %d", len(photos))
    logger.info("GUIDs already recorded: %d", len(already))
    logger.info("GUIDs to stamp this run: %d", len(new))

    if args.dry_run:
        logger.info("DRY RUN: no changes written.")
        return
    if not new:
        logger.info("Nothing to backfill; all album GUIDs already recorded.")
        return

    for guid in new:
        store.mark_guid_processed(guid, None)

    logger.info("Stamped %d GUID(s). Recorded total is now %d.", len(new), len(store.seen_guids()))
    logger.info("Next sync will only download photos added after this run.")


if __name__ == "__main__":
    main()
