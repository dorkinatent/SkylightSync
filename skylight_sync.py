#!/usr/bin/env python3

import argparse
import logging
import os
import sys
import time
from datetime import datetime

from dotenv import load_dotenv

from email_sender import EmailSender
from icloud_scraper import ICloudPhotoScraper, InitialSyncRefused

logger = logging.getLogger(__name__)

# Gmail rejects messages over 25 MB. base64 attachment encoding inflates size by
# ~37%, so keep raw attachment bytes per email comfortably under that.
MAX_ATTACHMENT_BYTES_PER_EMAIL = 18 * 1024 * 1024

# Limits the blast radius if dedup state is ever lost or iCloud GUIDs change, and
# keeps a big backlog within Gmail's daily sending limit. Well above a normal day;
# anything over the cap just goes out on later runs.
DEFAULT_MAX_PER_RUN = 200


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


def _sync(
    scraper: ICloudPhotoScraper,
    email_sender: EmailSender,
    recipient: str,
    batch_size: int,
    keep_photos: bool,
    max_per_run: int,
    allow_initial_sync: bool,
) -> bool:
    pending = scraper.scrape_photos(max_photos=max_per_run, allow_initial_sync=allow_initial_sync)
    if scraper.download_failures:
        logger.error("%d photo(s) failed to download; will retry next run", scraper.download_failures)
    if not pending:
        return scraper.download_failures == 0

    logger.info("Found %d new photo(s) to send", len(pending))
    count_cap = batch_size if batch_size > 0 else len(pending)
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
        if not keep_photos:
            _remove_files([item["path"]])

    sent = 0
    send_failed = False
    for i, batch in enumerate(batches, 1):
        subject = f"New photos from iCloud album - batch {i}/{len(batches)}"
        if not email_sender.send_photos(recipient, [b["path"] for b in batch], subject):
            remaining = sum(len(b) for b in batches[i - 1 :])
            logger.error("Batch %d failed to send; leaving %d photo(s) for the next run", i, remaining)
            send_failed = True
            break

        # Commit only after a confirmed send, so a failure never
        # orphans photos as "seen but never delivered".
        for item in batch:
            scraper.state_store.commit_photo(
                photo_guid=item["guid"],
                photo_hash=item["hash"],
                filename=item["filename"],
                url=item["url"],
                normalized_url=item["normalized"],
                timestamp=item["timestamp"],
            )
            if not keep_photos:
                _remove_files([item["path"]])
        sent += len(batch)
        logger.info("Sent batch %d/%d (%d photo(s)); total sent %d", i, len(batches), len(batch), sent)

    logger.info("Sync complete: sent %d of %d new photo(s) to %s", sent, len(pending), recipient)
    return not send_failed and scraper.download_failures == 0


def run_sync(
    scraper: ICloudPhotoScraper,
    email_sender: EmailSender,
    recipient: str,
    batch_size: int = 5,
    keep_photos: bool = False,
    max_per_run: int = DEFAULT_MAX_PER_RUN,
    allow_initial_sync: bool = False,
) -> bool:
    """Run one sync. Returns False if anything failed and needs attention.

    Skipping because another sync is running isn't a failure: that run reports
    its own result."""
    logger.info("Checking for new photos...")
    try:
        with scraper.state_store.exclusive_run() as acquired:
            if not acquired:
                logger.warning("Another sync is already running; skipping this one")
                return True
            return _sync(
                scraper, email_sender, recipient, batch_size, keep_photos, max_per_run, allow_initial_sync
            )
    except InitialSyncRefused as e:
        logger.error("%s", e)
        return False
    except Exception:
        logger.exception("Error during sync")
        return False


def main() -> None:
    parser = argparse.ArgumentParser(description="Sync iCloud shared album photos to email")
    parser.add_argument("--once", action="store_true", help="Run once instead of continuously")
    parser.add_argument("--interval", type=int, default=3600, help="Check interval in seconds (default: 3600)")
    parser.add_argument("--batch-size", type=int, default=5, help="Number of photos per email (default: 5)")
    parser.add_argument("--keep-photos", action="store_true", help="Keep downloaded photos after sending")
    parser.add_argument(
        "--max-per-run",
        type=int,
        default=DEFAULT_MAX_PER_RUN,
        help=f"Most photos to send per sync; the rest wait for later runs, 0 = no limit "
        f"(default: {DEFAULT_MAX_PER_RUN})",
    )
    parser.add_argument(
        "--allow-initial-sync",
        action="store_true",
        help="Allow sending when the dedup database is empty (a first run that sends the whole album)",
    )
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

    def sync_once() -> bool:
        return run_sync(
            scraper,
            email_sender,
            RECIPIENT_EMAIL,
            batch_size=args.batch_size,
            keep_photos=args.keep_photos,
            max_per_run=args.max_per_run,
            allow_initial_sync=args.allow_initial_sync,
        )

    if args.once:
        sys.exit(0 if sync_once() else 1)

    while True:
        sync_once()
        logger.info("Waiting %d seconds until next check...", args.interval)
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
