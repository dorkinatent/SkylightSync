#!/usr/bin/env python3

import argparse
import logging
import os
import sys
import time
from datetime import datetime

from dotenv import load_dotenv

from email_sender import EmailSender
from icloud_scraper import ICloudPhotoScraper

logger = logging.getLogger(__name__)

# Gmail rejects messages over 25 MB. base64 attachment encoding inflates size by
# ~37%, so keep raw attachment bytes per email comfortably under that.
MAX_ATTACHMENT_BYTES_PER_EMAIL = 18 * 1024 * 1024


def _remove_files(paths: list[str]) -> None:
    for path in paths:
        try:
            os.remove(path)
        except OSError as e:
            logger.warning("Could not delete %s: %s", path, e)


def plan_batches(
    pending: list[dict], max_count: int, max_bytes: int
) -> tuple[list[list[dict]], list[dict]]:
    """Pack pending photos into batches that stay under both the per-email count
    and byte limits. Any single photo larger than the byte limit can't be
    emailed at all and is returned separately as ``oversized``."""
    oversized = [p for p in pending if p["size"] > max_bytes]
    fits = [p for p in pending if p["size"] <= max_bytes]

    batches: list[list[dict]] = []
    batch: list[dict] = []
    size = 0
    for item in fits:
        over_count = max_count > 0 and len(batch) >= max_count
        over_bytes = size + item["size"] > max_bytes
        if batch and (over_count or over_bytes):
            batches.append(batch)
            batch, size = [], 0
        batch.append(item)
        size += item["size"]
    if batch:
        batches.append(batch)
    return batches, oversized


def main() -> None:
    parser = argparse.ArgumentParser(description="Sync iCloud shared album photos to email")
    parser.add_argument("--once", action="store_true", help="Run once instead of continuously")
    parser.add_argument("--interval", type=int, default=3600, help="Check interval in seconds (default: 3600)")
    parser.add_argument("--batch-size", type=int, default=5, help="Number of photos per email (default: 5)")
    parser.add_argument("--keep-photos", action="store_true", help="Keep downloaded photos after sending")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    load_dotenv()

    ICLOUD_ALBUM_URL = os.getenv("ICLOUD_ALBUM_URL")
    # Accept legacy variable names (TO_EMAIL/SMTP_USERNAME/SMTP_PASSWORD) as aliases.
    RECIPIENT_EMAIL = os.getenv("RECIPIENT_EMAIL") or os.getenv("TO_EMAIL")

    if not ICLOUD_ALBUM_URL or not RECIPIENT_EMAIL:
        logger.error("Album URL and recipient email not configured!")
        logger.error("Please set ICLOUD_ALBUM_URL and RECIPIENT_EMAIL (or TO_EMAIL)")
        sys.exit(1)

    SMTP_SERVER = os.getenv("SMTP_SERVER", "smtp.gmail.com")
    SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
    SENDER_EMAIL = os.getenv("SENDER_EMAIL") or os.getenv("SMTP_USERNAME")
    SENDER_PASSWORD = os.getenv("SENDER_PASSWORD") or os.getenv("SMTP_PASSWORD")

    if not SENDER_EMAIL or not SENDER_PASSWORD:
        logger.error("Email credentials not configured!")
        logger.error("Please set SENDER_EMAIL/SENDER_PASSWORD (or SMTP_USERNAME/SMTP_PASSWORD)")
        sys.exit(1)

    scraper = ICloudPhotoScraper(ICLOUD_ALBUM_URL)
    email_sender = EmailSender(SMTP_SERVER, SMTP_PORT, SENDER_EMAIL, SENDER_PASSWORD)

    logger.info("SkylightSync started at %s", datetime.now())
    logger.info("Album URL: %s", ICLOUD_ALBUM_URL)
    logger.info("Recipient: %s", RECIPIENT_EMAIL)
    logger.info("Check interval: %d seconds", args.interval)
    logger.info("Batch size: %d photos per email", args.batch_size)

    def commit_and_cleanup(item: dict) -> None:
        scraper.state_store.commit_photo(
            photo_guid=item["guid"],
            photo_hash=item["hash"],
            filename=item["filename"],
            url=item["url"],
            normalized_url=item["normalized"],
            timestamp=item["timestamp"],
        )
        if not args.keep_photos:
            _remove_files([item["path"]])

    def sync_photos() -> None:
        logger.info("Checking for new photos...")

        try:
            pending = scraper.scrape_photos()
            if not pending:
                logger.info("No new photos found")
                return

            logger.info("Found %d new photo(s) to send", len(pending))
            count_cap = args.batch_size if args.batch_size > 0 else len(pending)
            batches, oversized = plan_batches(pending, count_cap, MAX_ATTACHMENT_BYTES_PER_EMAIL)

            # A single photo too large to email can never be delivered. Mark only
            # its GUID as handled so it isn't retried forever — do NOT record it
            # in the delivered-photos/hash state (it was never sent).
            for item in oversized:
                logger.warning(
                    "Photo %s is %d bytes — too large to email; skipping permanently",
                    item["filename"],
                    item["size"],
                )
                scraper.state_store.mark_guid_processed(item["guid"], None)
                if not args.keep_photos:
                    _remove_files([item["path"]])

            sent = 0
            for i, batch in enumerate(batches, 1):
                subject = f"New photos from iCloud album - batch {i}/{len(batches)}"
                if email_sender.send_photos(RECIPIENT_EMAIL, [b["path"] for b in batch], subject):
                    # Commit only after a confirmed send, so a failure never
                    # orphans photos as "seen but never delivered".
                    for item in batch:
                        commit_and_cleanup(item)
                    sent += len(batch)
                    logger.info("Sent batch %d/%d (%d photo(s)); total sent %d",
                                i, len(batches), len(batch), sent)
                else:
                    remaining = sum(len(b) for b in batches[i - 1:])
                    logger.error(
                        "Batch %d failed to send; leaving %d photo(s) for the next run",
                        i, remaining,
                    )
                    break

            logger.info("Sync complete: sent %d of %d new photo(s) to %s",
                        sent, len(pending), RECIPIENT_EMAIL)

        except Exception as e:
            logger.error("Error during sync: %s", e)

    if args.once:
        sync_photos()
    else:
        while True:
            sync_photos()
            logger.info("Waiting %d seconds until next check...", args.interval)
            time.sleep(args.interval)


if __name__ == "__main__":
    main()
