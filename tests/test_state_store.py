import json

from state_store import StateStore, normalize_url


def test_normalize_url_drops_query_string():
    assert normalize_url("https://h.com/S/ABC/p.jpg?s=tok") == "S/ABC/p.jpg"
    assert normalize_url("https://h.com/S/ABC/p.jpg?s=other") == "S/ABC/p.jpg"


def test_url_dedup(tmp_path):
    store = StateStore(str(tmp_path / "state.db"))
    assert store.is_url_processed("S/ABC/p.jpg") is False
    store.mark_url_processed("S/ABC/p.jpg", "hash-1", "https://h.com/S/ABC/p.jpg?s=tok")
    assert store.is_url_processed("S/ABC/p.jpg") is True


def test_photo_hash_dedup(tmp_path):
    store = StateStore(str(tmp_path / "state.db"))
    assert store.has_hash("hash-1") is False
    store.add_photo("hash-1", "photo_1.jpg", "https://h.com/p.jpg", "20260101_000000")
    assert store.has_hash("hash-1") is True
    assert "hash-1" in store.seen_hashes()


def test_add_photo_is_idempotent(tmp_path):
    store = StateStore(str(tmp_path / "state.db"))
    store.add_photo("hash-1", "a.jpg", "https://h.com/a.jpg", "20260101_000000")
    store.add_photo("hash-1", "a-renamed.jpg", "https://h.com/a.jpg", "20260101_000001")
    assert store.seen_hashes() == {"hash-1"}


def test_migrates_legacy_processed_photos_json(tmp_path):
    (tmp_path / "processed_photos.json").write_text(
        json.dumps(
            {
                "hash-1": {
                    "filename": "photo_1.jpg",
                    "timestamp": "20260101_000000",
                    "url": "https://cvws.icloud-content.com/S/ABC/photo_1.jpg?s=tok",
                }
            }
        ),
        encoding="utf-8",
    )

    store = StateStore(str(tmp_path / "state.db"), legacy_data_dir=str(tmp_path))

    assert store.has_hash("hash-1") is True
    # The legacy photo's URL is migrated so it won't be re-downloaded.
    assert store.is_url_processed("S/ABC/photo_1.jpg") is True


def test_migration_runs_only_when_db_empty(tmp_path):
    store = StateStore(str(tmp_path / "state.db"))
    store.add_photo("existing", "e.jpg", None, "20260101_000000")

    # Write a legacy file after the DB already has rows; it must be ignored.
    (tmp_path / "processed_photos.json").write_text(
        json.dumps({"hash-late": {"filename": "late.jpg", "timestamp": "x", "url": None}}),
        encoding="utf-8",
    )
    store2 = StateStore(str(tmp_path / "state.db"), legacy_data_dir=str(tmp_path))
    assert store2.has_hash("hash-late") is False
