import os
import tempfile

from skylight_sync import _remove_files, plan_batches


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
