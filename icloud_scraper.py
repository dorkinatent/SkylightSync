import hashlib
import logging
import os
import time
from datetime import datetime

import requests
from selenium import webdriver
from selenium.common.exceptions import TimeoutException
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
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

    def _collect_photo_urls(self, driver: webdriver.Chrome) -> list[str]:
        driver.get(self.album_url)
        time.sleep(5)

        try:
            WebDriverWait(driver, 20).until(
                EC.presence_of_element_located((By.CLASS_NAME, "image-container"))
            )
        except TimeoutException:
            logger.warning("Primary selector not found, trying alternative photo elements...")

        last_height = driver.execute_script("return document.body.scrollHeight")
        while True:
            driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
            time.sleep(2)
            new_height = driver.execute_script("return document.body.scrollHeight")
            if new_height == last_height:
                break
            last_height = new_height

        urls: list[str] = []
        for element in driver.find_elements(By.TAG_NAME, "img"):
            src = element.get_attribute("src")
            if not src or "icloud" not in src:
                continue
            if any(keyword in src for keyword in ["thumb", "icon", "logo"]):
                continue
            urls.append(src)
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
