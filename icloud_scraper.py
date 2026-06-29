import hashlib
import logging
import os
import time
from datetime import datetime

import requests
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from tenacity import retry, stop_after_attempt, wait_exponential

from state_store import StateStore, normalize_url

logger = logging.getLogger(__name__)


class ICloudPhotoScraper:
    def __init__(
        self,
        album_url: str,
        download_dir: str = "downloads",
        data_dir: str = "data",
        state_store: StateStore | None = None,
    ) -> None:
        self.album_url = album_url
        self.download_dir = download_dir
        self.data_dir = data_dir

        os.makedirs(self.data_dir, exist_ok=True)
        os.makedirs(self.download_dir, exist_ok=True)

        self.state_store = state_store or StateStore(os.path.join(data_dir, "skylight.db"))

    def normalize_url(self, url: str) -> str:
        return normalize_url(url)

    def get_photo_hash(self, photo_data: bytes) -> str:
        return hashlib.md5(photo_data).hexdigest()

    def setup_driver(self) -> webdriver.Chrome:
        chrome_options = Options()
        chrome_options.add_argument("--headless")
        chrome_options.add_argument("--no-sandbox")
        chrome_options.add_argument("--disable-dev-shm-usage")
        chrome_options.add_argument("--disable-gpu")
        chrome_options.add_argument("--window-size=1920,1080")
        chrome_options.add_argument("--disable-blink-features=AutomationControlled")
        chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
        chrome_options.add_experimental_option("useAutomationExtension", False)

        if os.environ.get("CHROME_BIN"):
            chrome_options.binary_location = os.environ["CHROME_BIN"]

        return webdriver.Chrome(options=chrome_options)

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10), reraise=True)
    def _download_image(self, url: str) -> bytes:
        response = requests.get(url, timeout=30)
        response.raise_for_status()
        return response.content

    def _open_first_photo(self, driver: webdriver.Chrome) -> bool:
        """Open the carousel by clicking the first photo. Returns False if none found."""
        view_buttons = driver.find_elements(By.CSS_SELECTOR, "[role='button'][aria-label*='of']")
        if view_buttons:
            view_buttons[0].click()
            time.sleep(1)
            return True

        containers = driver.find_elements(
            By.CSS_SELECTOR,
            ".x-stream-photo-grid-item-view, [class*='photo'], [class*='grid-item']",
        )
        if containers:
            containers[0].click()
            time.sleep(1)
            return True

        logger.warning("No clickable photo elements found on album page")
        return False

    def _collect_photo_urls(self, driver: webdriver.Chrome, max_photos: int = 5000) -> list[str]:
        """Step through the iCloud carousel collecting each photo's URL.

        The shared-album grid is virtualized (only a few <img> tags exist at
        once), so we open a photo and advance through the carousel, gathering
        unique image URLs until we loop back to the first one.
        """
        driver.get(self.album_url)
        time.sleep(5)

        if not self._open_first_photo(driver):
            return []

        urls: list[str] = []
        seen: set[str] = set()
        first_url: str | None = None
        consecutive_duplicates = 0

        for count in range(max_photos):
            time.sleep(0.25)
            carousel_images = driver.find_elements(By.CSS_SELECTOR, "img[src*='icloud']")
            if not carousel_images:
                logger.info("No image in carousel at photo %d, stopping", count + 1)
                break

            image_url = carousel_images[0].get_attribute("src")
            if image_url and image_url not in seen:
                seen.add(image_url)
                urls.append(image_url)
                consecutive_duplicates = 0
                if first_url is None:
                    first_url = image_url
            elif image_url:
                consecutive_duplicates += 1
                if consecutive_duplicates > 10:
                    logger.info("Stopping carousel: duplicate loop detected")
                    break

            if first_url and image_url == first_url and count > 0:
                logger.info("Reached the first photo again, carousel complete")
                break

            try:
                driver.find_element(
                    By.CSS_SELECTOR, "[aria-label='Next'], .next, [data-testid='next']"
                ).click()
            except Exception:
                driver.find_element(By.TAG_NAME, "body").send_keys(Keys.ARROW_RIGHT)
            time.sleep(0.1)

        logger.info("Carousel navigation finished: %d unique photo URLs", len(urls))
        return urls

    def scrape_photos(self) -> list[str]:
        driver = self.setup_driver()
        new_photos: list[str] = []

        try:
            logger.info("Loading album: %s", self.album_url)
            urls = self._collect_photo_urls(driver)
            logger.info("Found %d potential photo elements", len(urls))

            seen_hashes = self.state_store.seen_hashes()

            for idx, src in enumerate(urls):
                normalized = self.normalize_url(src)

                # URL-level dedup: skip already-seen photos without downloading.
                if self.state_store.is_url_processed(normalized):
                    logger.debug("URL already processed, skipping download: %s", normalized)
                    continue

                try:
                    photo_data = self._download_image(src)
                except Exception as e:
                    logger.error("Error downloading photo %d: %s", idx, e)
                    continue

                photo_hash = self.get_photo_hash(photo_data)

                # Content-level dedup: same image served under a new signed URL.
                if photo_hash in seen_hashes:
                    self.state_store.mark_url_processed(normalized, photo_hash, src)
                    logger.debug("Photo already processed (hash: %s...)", photo_hash[:8])
                    continue

                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                filename = f"photo_{timestamp}_{idx}.jpg"
                filepath = os.path.join(self.download_dir, filename)
                with open(filepath, "wb") as f:
                    f.write(photo_data)

                self.state_store.add_photo(photo_hash, filename, src, timestamp)
                self.state_store.mark_url_processed(normalized, photo_hash, src)
                seen_hashes.add(photo_hash)
                new_photos.append(filepath)
                logger.info("Downloaded new photo: %s", filename)

        except Exception as e:
            logger.error("Error during scraping: %s", e)

        finally:
            driver.quit()

        return new_photos
