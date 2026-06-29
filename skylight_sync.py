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


def cleanup_photos(photo_paths: list[str]) -> None:
    for path in photo_paths:
        try:
            os.remove(path)
        except OSError as e:
            logger.warning("Could not delete %s: %s", path, e)


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
    RECIPIENT_EMAIL = os.getenv("RECIPIENT_EMAIL")

    if not ICLOUD_ALBUM_URL or not RECIPIENT_EMAIL:
        logger.error("Album URL and recipient email not configured!")
        logger.error("Please set ICLOUD_ALBUM_URL and RECIPIENT_EMAIL environment variables")
        sys.exit(1)

    SMTP_SERVER = os.getenv("SMTP_SERVER", "smtp.gmail.com")
    SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
    SENDER_EMAIL = os.getenv("SENDER_EMAIL")
    SENDER_PASSWORD = os.getenv("SENDER_PASSWORD")

    if not SENDER_EMAIL or not SENDER_PASSWORD:
        logger.error("Email credentials not configured!")
        logger.error("Please set SENDER_EMAIL and SENDER_PASSWORD environment variables")
        sys.exit(1)

    scraper = ICloudPhotoScraper(ICLOUD_ALBUM_URL)
    email_sender = EmailSender(SMTP_SERVER, SMTP_PORT, SENDER_EMAIL, SENDER_PASSWORD)

    logger.info("SkylightSync started at %s", datetime.now())
    logger.info("Album URL: %s", ICLOUD_ALBUM_URL)
    logger.info("Recipient: %s", RECIPIENT_EMAIL)
    logger.info("Check interval: %d seconds", args.interval)
    logger.info("Batch size: %d photos per email", args.batch_size)

    def sync_photos() -> None:
        logger.info("Checking for new photos...")

        try:
            new_photos = scraper.scrape_photos()

            if new_photos:
                logger.info("Found %d new photo(s)", len(new_photos))

                if args.batch_size > 0:
                    success = email_sender.send_photos_in_batches(
                        RECIPIENT_EMAIL,
                        new_photos,
                        batch_size=args.batch_size,
                    )
                else:
                    success = email_sender.send_photos(RECIPIENT_EMAIL, new_photos)

                if success:
                    logger.info("Successfully sent %d photo(s) to %s", len(new_photos), RECIPIENT_EMAIL)
                    if not args.keep_photos:
                        cleanup_photos(new_photos)
                else:
                    logger.error("Failed to send some or all photos")
            else:
                logger.info("No new photos found")

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
