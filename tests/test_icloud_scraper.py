import os
import tempfile

import pytest

from icloud_scraper import ICloudPhotoScraper, InitialSyncRefused


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

            downloaded = scraper.scrape_photos(allow_initial_sync=True)

            assert downloaded == []
            assert scraper.state_store.seen_guids() == {"G1", "G2"}

            # A second run sees them as already processed and does no work.
            scraper._resolve_asset_urls = lambda guids: (_ for _ in ()).throw(
                AssertionError("should not resolve already-processed GUIDs")
            )
            assert scraper.scrape_photos(allow_initial_sync=True) == []


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
            assert scraper.scrape_photos(allow_initial_sync=True) == []
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

            pending = scraper.scrape_photos(allow_initial_sync=True)

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
            assert scraper.scrape_photos(allow_initial_sync=True) == []


class TestSameRunDuplicate:
    """Two GUIDs with identical content in one run: send once, don't commit the
    alias before the original is delivered."""

    def test_same_run_duplicate_is_deferred_not_committed(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            scraper = _scraper(tmpdir)
            scraper._fetch_stream = lambda: [
                {"photoGuid": "A", "derivatives": {"1": {"checksum": "ca", "fileSize": "3"}},
                 "dateCreated": "2026-01-01T00:00:00Z"},
                {"photoGuid": "B", "derivatives": {"1": {"checksum": "cb", "fileSize": "3"}},
                 "dateCreated": "2026-01-02T00:00:00Z"},
            ]
            scraper._resolve_asset_urls = lambda guids: {
                "ca": "https://x/A.JPG?s=t", "cb": "https://x/B.JPG?s=t"
            }
            scraper._download = lambda url: b"identical-bytes"  # same hash for both

            pending = scraper.scrape_photos(allow_initial_sync=True)

            # Only the first is queued; the duplicate is skipped.
            assert len(pending) == 1
            # Neither GUID is committed yet (original still pending delivery).
            assert scraper.state_store.seen_guids() == set()


def _stub_album(scraper: ICloudPhotoScraper, count: int) -> None:
    """An album of ``count`` photos with unique content, oldest first by name."""
    scraper._fetch_stream = lambda: [
        {"photoGuid": f"P{i}", "derivatives": {"1": {"checksum": f"c{i}", "fileSize": "3"}},
         "dateCreated": f"2026-01-0{i + 1}T00:00:00Z"}
        for i in range(count)
    ]
    scraper._resolve_asset_urls = lambda guids: {
        f"c{g[1:]}": f"https://x/{g}.JPG?s=t" for g in guids
    }
    scraper._download = lambda url: url.encode()


class TestInitialSyncGuard:
    def test_empty_state_refuses_without_downloading_or_marking(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            scraper = _scraper(tmpdir)
            _stub_album(scraper, 2)
            scraper._download = lambda url: pytest.fail("must not download")

            with pytest.raises(InitialSyncRefused):
                scraper.scrape_photos()

            assert scraper.state_store.seen_guids() == set()

    def test_existing_state_passes(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            scraper = _scraper(tmpdir)
            _stub_album(scraper, 2)
            scraper.state_store.mark_guid_processed("P0", None)

            assert [p["guid"] for p in scraper.scrape_photos()] == ["P1"]

    def test_empty_album_on_empty_state_is_not_refused(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            scraper = _scraper(tmpdir)
            _stub_album(scraper, 0)

            assert scraper.scrape_photos() == []


class TestMaxPhotos:
    def test_takes_oldest_first_up_to_cap(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            scraper = _scraper(tmpdir)
            _stub_album(scraper, 3)

            pending = scraper.scrape_photos(max_photos=2, allow_initial_sync=True)

            assert [p["guid"] for p in pending] == ["P0", "P1"]

    def test_zero_means_no_limit(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            scraper = _scraper(tmpdir)
            _stub_album(scraper, 3)

            assert len(scraper.scrape_photos(max_photos=0, allow_initial_sync=True)) == 3


class TestFailureReporting:
    def test_fetch_failure_raises_instead_of_looking_like_no_photos(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            scraper = _scraper(tmpdir)

            def boom():
                raise ConnectionError("album unreachable")

            scraper._fetch_stream = boom
            with pytest.raises(ConnectionError):
                scraper.scrape_photos()

    def test_download_failures_are_counted_and_left_unmarked(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            scraper = _scraper(tmpdir)
            _stub_album(scraper, 1)

            def boom(url):
                raise ConnectionError("download failed")

            scraper._download = boom
            assert scraper.scrape_photos(allow_initial_sync=True) == []
            assert scraper.download_failures == 1
            assert scraper.state_store.seen_guids() == set()


class TestDirectories:
    def test_creates_directories(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            data_dir = os.path.join(tmpdir, "nested", "data")
            dl_dir = os.path.join(tmpdir, "nested", "downloads")
            ICloudPhotoScraper("https://example.com", download_dir=dl_dir, data_dir=data_dir)
            assert os.path.isdir(data_dir)
            assert os.path.isdir(dl_dir)
