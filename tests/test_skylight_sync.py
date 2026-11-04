import os
import tempfile
from unittest.mock import MagicMock, patch

import pytest

import skylight_sync
from icloud_scraper import ICloudPhotoScraper
from skylight_sync import _remove_files, plan_batches, run_sync
from state_store import StateStore


def _item(size: int, name: str = "p.jpg") -> dict:
    return {"path": f"/tmp/{name}", "size": size, "filename": name}


class TestRemoveFiles:
    def test_deletes_files(self):
        paths = []
        for _ in range(3):
            f = tempfile.NamedTemporaryFile(suffix=".jpg", delete=False)
            f.write(b"data")
            f.close()
            paths.append(f.name)

        _remove_files(paths)
        for p in paths:
            assert not os.path.exists(p)

    def test_missing_file_no_error(self):
        _remove_files(["/nonexistent/file.jpg"])


class TestPlanBatches:
    def test_splits_by_count(self):
        items = [_item(10) for _ in range(5)]
        batches, oversized = plan_batches(items, max_count=2, max_bytes=1_000_000)
        assert [len(b) for b in batches] == [2, 2, 1]
        assert oversized == []

    def test_splits_by_size(self):
        # Three 6-byte items with an 10-byte cap -> at most one per batch pair.
        items = [_item(6) for _ in range(3)]
        batches, oversized = plan_batches(items, max_count=100, max_bytes=10)
        assert [len(b) for b in batches] == [1, 1, 1]

    def test_oversized_separated(self):
        items = [_item(5), _item(50), _item(5)]
        batches, oversized = plan_batches(items, max_count=100, max_bytes=10)
        assert len(oversized) == 1 and oversized[0]["size"] == 50
        assert sum(len(b) for b in batches) == 2

    def test_size_and_count_together(self):
        items = [_item(4) for _ in range(4)]
        # count cap 3 hits before the byte cap (12 fits in 20)
        batches, _ = plan_batches(items, max_count=3, max_bytes=20)
        assert [len(b) for b in batches] == [3, 1]


@pytest.fixture
def scraper(tmp_path):
    """Three new photos, on top of existing dedup state."""
    s = ICloudPhotoScraper("https://example.com", download_dir=str(tmp_path), data_dir=str(tmp_path))
    s.state_store.mark_guid_processed("OLD", None)
    s._fetch_stream = lambda: [
        {"photoGuid": f"P{i}", "derivatives": {"1": {"checksum": f"c{i}", "fileSize": "3"}},
         "dateCreated": f"2026-01-0{i + 1}T00:00:00Z"}
        for i in range(3)
    ]
    s._resolve_asset_urls = lambda guids: {f"c{g[1:]}": f"https://x/{g}.JPG?s=t" for g in guids}
    s._download = lambda url: url.encode()
    return s


@pytest.fixture
def sender():
    m = MagicMock()
    m.send_photos.return_value = True
    return m


def _sent_guids(scraper) -> set[str]:
    return scraper.state_store.seen_guids() - {"OLD"}


class TestRunSync:
    def test_success_commits_and_cleans_up(self, scraper, sender, tmp_path):
        assert run_sync(scraper, sender, "r@test.com") is True
        assert _sent_guids(scraper) == {"P0", "P1", "P2"}
        assert list(tmp_path.glob("*.JPG")) == []

    def test_send_failure_reports_failure_and_commits_nothing(self, scraper, sender):
        sender.send_photos.return_value = False

        assert run_sync(scraper, sender, "r@test.com") is False
        assert _sent_guids(scraper) == set()

    def test_later_batch_failure_keeps_earlier_commits(self, scraper, sender):
        sender.send_photos.side_effect = [True, False]

        assert run_sync(scraper, sender, "r@test.com", batch_size=1) is False
        assert _sent_guids(scraper) == {"P0"}

    def test_album_fetch_failure_reports_failure(self, scraper, sender):
        def boom():
            raise ConnectionError("album unreachable")

        scraper._fetch_stream = boom

        assert run_sync(scraper, sender, "r@test.com") is False
        sender.send_photos.assert_not_called()

    def test_partial_download_failure_sends_the_rest_but_reports_failure(self, scraper, sender):
        def flaky(url):
            if "P1" in url:
                raise ConnectionError("download failed")
            return url.encode()

        scraper._download = flaky

        assert run_sync(scraper, sender, "r@test.com") is False
        assert _sent_guids(scraper) == {"P0", "P2"}

    def test_no_new_photos_is_success(self, scraper, sender):
        scraper._fetch_stream = lambda: []

        assert run_sync(scraper, sender, "r@test.com") is True
        sender.send_photos.assert_not_called()

    def test_oversized_photo_alone_is_not_a_failure(self, scraper, sender):
        with patch.object(skylight_sync, "MAX_ATTACHMENT_BYTES_PER_EMAIL", 1):
            assert run_sync(scraper, sender, "r@test.com") is True
        sender.send_photos.assert_not_called()
        assert _sent_guids(scraper) == {"P0", "P1", "P2"}
        assert scraper.state_store.seen_hashes() == set()


class TestEmptyStateSafety:
    def _fresh(self, scraper, tmp_path):
        scraper.state_store = StateStore(str(tmp_path / "fresh" / "skylight.db"))
        return scraper

    def test_refuses_and_sends_nothing(self, scraper, sender, tmp_path):
        scraper = self._fresh(scraper, tmp_path)

        assert run_sync(scraper, sender, "r@test.com") is False
        sender.send_photos.assert_not_called()
        assert scraper.state_store.seen_guids() == set()

    def test_explicit_flag_sends_but_still_honors_the_cap(self, scraper, sender, tmp_path):
        scraper = self._fresh(scraper, tmp_path)

        assert run_sync(scraper, sender, "r@test.com", max_per_run=2, allow_initial_sync=True) is True
        assert scraper.state_store.seen_guids() == {"P0", "P1"}


class TestPerRunCap:
    def test_backlog_drains_across_runs(self, scraper, sender):
        assert run_sync(scraper, sender, "r@test.com", max_per_run=2) is True
        assert _sent_guids(scraper) == {"P0", "P1"}

        assert run_sync(scraper, sender, "r@test.com", max_per_run=2) is True
        assert _sent_guids(scraper) == {"P0", "P1", "P2"}


class TestOverlappingRuns:
    def test_second_run_skips_without_sending(self, scraper, sender):
        other = StateStore(scraper.state_store.db_path)
        with other.exclusive_run() as acquired:
            assert acquired is True
            assert run_sync(scraper, sender, "r@test.com") is True
        sender.send_photos.assert_not_called()

    def test_lock_is_released_afterwards(self, scraper, sender):
        with scraper.state_store.exclusive_run():
            pass
        assert run_sync(scraper, sender, "r@test.com") is True
        assert _sent_guids(scraper) == {"P0", "P1", "P2"}


class TestExitCode:
    @pytest.mark.parametrize("ok,code", [(True, 0), (False, 1)])
    def test_once_exit_code_reflects_result(self, monkeypatch, ok, code):
        monkeypatch.setattr("sys.argv", ["skylight_sync.py", "--once"])
        for key in ("ICLOUD_ALBUM_URL", "RECIPIENT_EMAIL", "SENDER_EMAIL", "SENDER_PASSWORD"):
            monkeypatch.setenv(key, "x")
        with (
            patch.object(skylight_sync, "load_dotenv"),
            patch.object(skylight_sync, "ICloudPhotoScraper"),
            patch.object(skylight_sync, "EmailSender"),
            patch.object(skylight_sync, "run_sync", return_value=ok),
            pytest.raises(SystemExit) as exit_info,
        ):
            skylight_sync.main()
        assert exit_info.value.code == code


class TestCLIParsing:
    def test_default_args(self):
        import argparse
        parser = argparse.ArgumentParser()
        parser.add_argument("--once", action="store_true")
        parser.add_argument("--interval", type=int, default=3600)
        parser.add_argument("--batch-size", type=int, default=5)
        parser.add_argument("--keep-photos", action="store_true")
        args = parser.parse_args([])
        assert args.once is False
        assert args.interval == 3600
        assert args.batch_size == 5
        assert args.keep_photos is False

    def test_all_flags(self):
        import argparse
        parser = argparse.ArgumentParser()
        parser.add_argument("--once", action="store_true")
        parser.add_argument("--interval", type=int, default=3600)
        parser.add_argument("--batch-size", type=int, default=5)
        parser.add_argument("--keep-photos", action="store_true")
        args = parser.parse_args(["--once", "--interval", "120", "--batch-size", "10", "--keep-photos"])
        assert args.once is True
        assert args.interval == 120
        assert args.batch_size == 10
        assert args.keep_photos is True
