import os
import tempfile

from icloud_scraper import ICloudPhotoScraper


def _scraper(tmpdir: str) -> ICloudPhotoScraper:
    return ICloudPhotoScraper("https://example.com", download_dir=tmpdir, data_dir=tmpdir)


class TestPhotoHash:
    def test_same_content_same_hash(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            scraper = _scraper(tmpdir)
            data = b"fake image content"
            assert scraper.get_photo_hash(data) == scraper.get_photo_hash(data)

    def test_different_content_different_hash(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            scraper = _scraper(tmpdir)
            assert scraper.get_photo_hash(b"image1") != scraper.get_photo_hash(b"image2")

    def test_hash_is_hex_string(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            h = _scraper(tmpdir).get_photo_hash(b"test")
            assert isinstance(h, str)
            assert len(h) == 32
            int(h, 16)  # should not raise


class TestNormalizeUrl:
    def test_strips_signed_query_string(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            scraper = _scraper(tmpdir)
            normalized = scraper.normalize_url(
                "https://cvws.icloud-content.com/S/ABC123/file.JPG?x=1&y=2&s=token"
            )
            assert normalized == "S/ABC123/file.JPG"

    def test_same_photo_different_token_normalizes_equal(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            scraper = _scraper(tmpdir)
            a = scraper.normalize_url("https://x.com/S/ABC/p.jpg?s=tok1")
            b = scraper.normalize_url("https://x.com/S/ABC/p.jpg?s=tok2")
            assert a == b


class TestDirectories:
    def test_creates_directories(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            data_dir = os.path.join(tmpdir, "nested", "data")
            dl_dir = os.path.join(tmpdir, "nested", "downloads")
            ICloudPhotoScraper("https://example.com", download_dir=dl_dir, data_dir=data_dir)
            assert os.path.isdir(data_dir)
            assert os.path.isdir(dl_dir)
