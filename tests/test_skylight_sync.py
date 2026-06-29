import os
import tempfile

from skylight_sync import cleanup_photos


class TestCleanupPhotos:
    def test_deletes_files(self):
        paths = []
        for _ in range(3):
            f = tempfile.NamedTemporaryFile(suffix=".jpg", delete=False)
            f.write(b"data")
            f.close()
            paths.append(f.name)

        cleanup_photos(paths)
        for p in paths:
            assert not os.path.exists(p)

    def test_missing_file_no_error(self):
        cleanup_photos(["/nonexistent/file.jpg"])


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
