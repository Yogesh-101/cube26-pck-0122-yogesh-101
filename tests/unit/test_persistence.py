"""Inspection rows and photo paths must survive restart, redeploy, and code updates."""

from __future__ import annotations

from pathlib import Path

from app.api.routes import relativize_inspection_paths, resolve_inspection_image
from app.storage.database import (
    count_inspections,
    get_inspection,
    init_database,
    list_inspections,
    save_inspection,
)
from app.storage.durable import (
    append_inspection_mirror,
    iter_mirror,
    recover_missing_into_db,
)


def _sample_inspection(tmp_path: Path, photo: Path, iid: str = "insp-persist-1") -> dict:
    return {
        "inspection_id": iid,
        "status": "completed",
        "order": {
            "order_id": "ORD-1",
            "unit_id": "UNIT-1",
            "org_id": "org_demo_alpha",
            "channel": "shopify",
            "lines": [{"sku": "SKU-1", "quantity": 1}],
        },
        "observed_items": [{"sku": "SKU-1", "name": "Item", "observed_quantity": 1, "confidence": 0.9}],
        "checks": [{"check_key": "items_present", "verdict": "pass", "confidence": 0.9, "detail": "ok"}],
        "outcome": {"decision": "seal"},
        "catalogue": [],
        "images": [{"image_id": "img-1", "path": str(photo), "sha256": "abc"}],
        "evidence_record": {
            "record_id": "PCK-1",
            "images": [{"image_id": "img-1", "path": str(photo), "sha256": "abc"}],
            "overrides": [],
        },
    }


def test_save_and_reload_from_sqlite(tmp_path):
    db = tmp_path / "pack_manager.db"
    init_database(db)

    photo_dir = tmp_path / "images" / "org_demo_alpha" / "UNIT-1"
    photo_dir.mkdir(parents=True)
    photo = photo_dir / "shot.jpg"
    photo.write_bytes(b"jpeg-bytes")

    data = _sample_inspection(tmp_path, photo)
    relativize_inspection_paths(data, tmp_path)
    save_inspection(data, db)

    # Simulate a new process: no in-memory cache, fresh read from disk
    loaded = get_inspection("insp-persist-1", "org_demo_alpha", db)
    assert loaded is not None
    assert loaded["outcome"]["decision"] == "seal"
    assert loaded["order"]["org_id"] == "org_demo_alpha"
    assert loaded["images"][0]["path"] == "images/org_demo_alpha/UNIT-1/shot.jpg"
    assert loaded["evidence_record"]["images"][0]["path"].endswith("shot.jpg")

    listed = list_inspections("org_demo_alpha", db)
    assert len(listed) == 1
    assert listed[0]["inspection_id"] == "insp-persist-1"
    assert count_inspections("org_demo_alpha", db) == 1

    # Wrong org must not see the row
    assert get_inspection("insp-persist-1", "org_demo_bravo", db) is None


def test_relative_image_path_resolves_after_reload(tmp_path):
    photo_dir = tmp_path / "images" / "org_demo_alpha" / "UNIT-1"
    photo_dir.mkdir(parents=True)
    photo = photo_dir / "shot.jpg"
    photo.write_bytes(b"jpeg-bytes")

    data = _sample_inspection(tmp_path, photo)
    relativize_inspection_paths(data, tmp_path)

    found = resolve_inspection_image(data, "img-1", tmp_path)
    assert found == photo.resolve()


def test_upsert_preserves_created_at(tmp_path):
    db = tmp_path / "pack_manager.db"
    init_database(db)
    photo = tmp_path / "a.jpg"
    photo.write_bytes(b"x")
    data = _sample_inspection(tmp_path, photo)
    save_inspection(data, db)
    first = get_inspection("insp-persist-1", "org_demo_alpha", db)
    created = first["created_at"]

    data["status"] = "pending_review"
    save_inspection(data, db)
    second = get_inspection("insp-persist-1", "org_demo_alpha", db)
    assert second["created_at"] == created
    assert second["status"] == "pending_review"


def test_jsonl_mirror_survives_sqlite_wipe(tmp_path):
    """Redeploy / wiped SQLite: mirror on the volume restores past inspections."""
    db = tmp_path / "pack_manager.db"
    init_database(db)
    photo = tmp_path / "a.jpg"
    photo.write_bytes(b"jpeg")
    data = _sample_inspection(tmp_path, photo)
    relativize_inspection_paths(data, tmp_path)
    append_inspection_mirror(data, tmp_path)
    save_inspection(data, db)
    assert count_inspections(db_path=db) == 1

    # Simulate catastrophic SQLite loss (bad volume remount of only mirror, or wipe)
    db.unlink()
    for side in (tmp_path / "pack_manager.db-wal", tmp_path / "pack_manager.db-shm"):
        if side.exists():
            side.unlink()
    init_database(db)
    assert count_inspections(db_path=db) == 0
    assert list(iter_mirror(tmp_path))

    restored = recover_missing_into_db(tmp_path, db)
    assert restored == 1
    loaded = get_inspection("insp-persist-1", "org_demo_alpha", db)
    assert loaded is not None
    assert loaded["outcome"]["decision"] == "seal"


def test_mirror_keeps_history_across_updates(tmp_path):
    photo = tmp_path / "a.jpg"
    photo.write_bytes(b"x")
    data = _sample_inspection(tmp_path, photo)
    append_inspection_mirror(data, tmp_path)
    data["status"] = "pending_review"
    append_inspection_mirror(data, tmp_path)
    rows = list(iter_mirror(tmp_path))
    assert len(rows) == 1
    assert rows[0]["status"] == "pending_review"
