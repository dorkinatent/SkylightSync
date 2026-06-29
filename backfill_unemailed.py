#!/usr/bin/env python3
"""One-off backfill: deliver photos that were found but never emailed.

Photos with emailed=0 were scraped during the email outage but never sent.
Their original signed URLs are expired, so we re-scrape the album for fresh
URLs, then download + email the un-emailed matches in batches.

Idempotent and resumable: each photo is marked emailed=1 only after its batch
is accepted by the SMTP server, so a re-run (after a rate-limit or crash) skips
everything already delivered. A small delay between batches keeps Gmail happy.

Usage:
  python backfill_unemailed.py --dry-run        # scrape + match, no sending
  python backfill_unemailed.py                  # send all, batches of 5
  python backfill_unemailed.py --max-photos 25  # send just the first 25 (smoke test)
"""

import argparse
import logging
import os
import smtplib
import sqlite3
import sys
import time

from dotenv import load_dotenv

from email_sender import EmailSender
from icloud_scraper import ICloudPhotoScraper
from state_store import normalize_url

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("backfill")

DB_PATH = "data/skylight.db"
STAGE_DIR = "backfill"


def unemailed_by_normalized_url() -> dict[str, str]:
    """Map normalized URL -> photo_hash for every photo not yet emailed."""
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute(
        "SELECT photo_hash, url FROM photos WHERE emailed=0 AND url IS NOT NULL AND url<>''"
    ).fetchall()
    conn.close()
    return {normalize_url(url): photo_hash for photo_hash, url in rows}


def count_unemailed() -> int:
    conn = sqlite3.connect(DB_PATH)
    n = conn.execute("SELECT count(*) FROM photos WHERE emailed=0").fetchone()[0]
    conn.close()
    return n


def mark_emailed(photo_hashes: list[str]) -> None:
    conn = sqlite3.connect(DB_PATH)
    conn.executemany("UPDATE photos SET emailed=1 WHERE photo_hash=?", [(h,) for h in photo_hashes])
    conn.commit()
    conn.close()


def main() -> None:
    ap = argparse.ArgumentParser(description="Backfill un-emailed photos to the Skylight frame")
    ap.add_argument("--batch-size", type=int, default=5)
    ap.add_argument("--delay", type=float, default=4.0, help="seconds to wait between batches")
    ap.add_argument("--max-photos", type=int, default=0, help="cap photos sent this run (0 = all)")
    ap.add_argument("--dry-run", action="store_true", help="scrape + match only; send nothing")
    args = ap.parse_args()

    load_dotenv()
    album = os.getenv("ICLOUD_ALBUM_URL")
    recipient = os.getenv("RECIPIENT_EMAIL") or os.getenv("TO_EMAIL")
    sender = os.getenv("SENDER_EMAIL") or os.getenv("SMTP_USERNAME")
    password = os.getenv("SENDER_PASSWORD") or os.getenv("SMTP_PASSWORD")
    smtp_host = os.getenv("SMTP_SERVER", "smtp.gmail.com")
    smtp_port = int(os.getenv("SMTP_PORT", "587"))
    if not all([album, recipient, sender, password]):
        logger.error("Missing config (album/recipient/sender/password)")
        sys.exit(1)

    targets = unemailed_by_normalized_url()
    logger.info("Un-emailed photos with usable URLs: %d", len(targets))
    if not targets:
        logger.info("Nothing to backfill.")
        return

    os.makedirs(STAGE_DIR, exist_ok=True)
    scraper = ICloudPhotoScraper(album, download_dir=STAGE_DIR, data_dir="data")
    driver = scraper.setup_driver()
    try:
        logger.info("Re-scraping album for fresh URLs (full carousel pass)...")
        fresh_urls = scraper._collect_photo_urls(driver)
    finally:
        driver.quit()
    logger.info("Collected %d fresh URLs from the album", len(fresh_urls))

    # Match the album's current photos against the un-emailed set.
    to_send: list[tuple[str, str]] = []  # (fresh_url, photo_hash)
    picked: set[str] = set()
    for url in fresh_urls:
        photo_hash = targets.get(normalize_url(url))
        if photo_hash and photo_hash not in picked:
            picked.add(photo_hash)
            to_send.append((url, photo_hash))

    missing = len(targets) - len(to_send)
    logger.info(
        "Matched %d un-emailed photos still in the album (%d no longer present, unrecoverable)",
        len(to_send),
        missing,
    )
    if args.max_photos > 0:
        to_send = to_send[: args.max_photos]
        logger.info("Capping this run to %d photos", len(to_send))

    n_batches = (len(to_send) + args.batch_size - 1) // args.batch_size
    if args.dry_run:
        logger.info("DRY RUN: would send %d photos in %d batches. No email sent.", len(to_send), n_batches)
        return
    if not to_send:
        logger.info("No matching photos to send.")
        return

    logger.info("Sending %d photos in %d batches (delay %.1fs)...", len(to_send), n_batches, args.delay)
    emailer = EmailSender(smtp_host, smtp_port, sender, password)
    smtp = smtplib.SMTP(smtp_host, smtp_port, timeout=30)
    smtp.starttls()
    smtp.login(sender, password)

    sent_total = 0
    batch_paths: list[str] = []
    batch_hashes: list[str] = []
    batch_idx = 0

    def flush() -> bool:
        nonlocal sent_total, batch_idx
        if not batch_paths:
            return True
        batch_idx += 1
        subject = f"SkylightSync backfill {batch_idx}/{n_batches} - {len(batch_paths)} photo(s)"
        ok = emailer.send_photos(recipient, batch_paths, subject, server=smtp)
        if ok:
            mark_emailed(batch_hashes)
            sent_total += len(batch_paths)
            logger.info("Batch %d/%d sent (%d photos); running total=%d", batch_idx, n_batches, len(batch_paths), sent_total)
            for p in batch_paths:
                try:
                    os.remove(p)
                except OSError:
                    pass
        else:
            logger.error("Batch %d failed to send; stopping. Re-run to resume.", batch_idx)
        return ok

    try:
        stopped = False
        for url, photo_hash in to_send:
            try:
                data = scraper._download_image(url)
            except Exception as e:
                logger.warning("Download failed for %s (%s); skipping", photo_hash[:8], e)
                continue
            path = os.path.join(STAGE_DIR, f"{photo_hash}.jpg")
            with open(path, "wb") as f:
                f.write(data)
            batch_paths.append(path)
            batch_hashes.append(photo_hash)
            if len(batch_paths) >= args.batch_size:
                if not flush():
                    stopped = True
                    break
                batch_paths, batch_hashes = [], []
                time.sleep(args.delay)
        if not stopped:
            flush()
    finally:
        try:
            smtp.quit()
        except Exception:
            pass

    logger.info("Backfill run complete. Sent this run: %d. Remaining un-emailed: %d", sent_total, count_unemailed())


if __name__ == "__main__":
    main()
