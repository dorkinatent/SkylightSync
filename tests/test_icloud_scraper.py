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


class TestGuidDedupOnSkip:
    """Unsupported photos must still be recorded so they aren't retried forever."""

    def test_photos_without_usable_asset_are_marked_processed(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            scraper = _scraper(tmpdir)

            # G1: no derivatives at all. G2: has a derivative, but its checksum
            # won't resolve to a URL below.
            scraper._fetch_stream = lambda: [
                {"photoGuid": "G1", "derivatives": {}, "dateCreated": "2026-01-01T00:00:00Z"},
                {
                    "photoGuid": "G2",
                    "derivatives": {"100": {"checksum": "c2", "fileSize": "10"}},
                    "dateCreated": "2026-01-02T00:00:00Z",
                },
            ]
            scraper._resolve_asset_urls = lambda guids: {}  # nothing resolves

            downloaded = scraper.scrape_photos()

            assert downloaded == []
            assert scraper.state_store.seen_guids() == {"G1", "G2"}

            # A second run sees them as already processed and does no work.
            scraper._resolve_asset_urls = lambda guids: (_ for _ in ()).throw(
                AssertionError("should not resolve already-processed GUIDs")
            )
            assert scraper.scrape_photos() == []


class TestVideoSkip:
    """Videos are skipped (photos-only) and left unmarked so a later policy
    change could still pick them up."""

    def test_videos_are_ignored(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            scraper = _scraper(tmpdir)
            scraper._fetch_stream = lambda: [
                {"photoGuid": "V1", "mediaAssetType": "video",
                 "derivatives": {"720p": {"checksum": "vc", "fileSize": "999"}},
                 "dateCreated": "2026-01-01T00:00:00Z"},
            ]
            scraper._resolve_asset_urls = lambda guids: (_ for _ in ()).throw(
                AssertionError("videos should never be resolved")
            )
            assert scraper.scrape_photos() == []
            assert scraper.state_store.seen_guids() == set()  # not marked


class TestDeferredCommit:
    """A downloaded photo is returned but NOT committed until the caller sends it."""

    def test_successful_download_is_pending_not_committed(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            scraper = _scraper(tmpdir)
            scraper._fetch_stream = lambda: [
                {"photoGuid": "P1", "derivatives": {"100": {"checksum": "c1", "fileSize": "3"}},
                 "dateCreated": "2026-01-01T00:00:00Z"},
            ]
            scraper._resolve_asset_urls = lambda guids: {"c1": "https://x/IMG_1.JPG?s=t"}
            scraper._download = lambda url: b"img"

            pending = scraper.scrape_photos()

            assert len(pending) == 1
            item = pending[0]
            assert item["guid"] == "P1" and item["size"] == 3
            assert os.path.exists(item["path"])
            # Deferred: nothing committed to any dedup layer yet.
            assert scraper.state_store.seen_guids() == set()
            assert scraper.state_store.seen_hashes() == set()

            # Caller commits after a successful send.
            scraper.state_store.commit_photo(
                photo_guid=item["guid"], photo_hash=item["hash"], filename=item["filename"],
                url=item["url"], normalized_url=item["normalized"], timestamp=item["timestamp"],
            )
            assert scraper.state_store.seen_guids() == {"P1"}
            # Now it's skipped on the next run.
            assert scraper.scrape_photos() == []


class TestDirectories:
    def test_creates_directories(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            data_dir = os.path.join(tmpdir, "nested", "data")
            dl_dir = os.path.join(tmpdir, "nested", "downloads")
            ICloudPhotoScraper("https://example.com", download_dir=dl_dir, data_dir=data_dir)
            assert os.path.isdir(data_dir)
            assert os.path.isdir(dl_dir)
