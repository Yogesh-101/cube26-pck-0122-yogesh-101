"""
Append-only JSONL mirror + startup recovery.

SQLite is the primary store. The JSONL mirror on the same STORAGE_ROOT volume
guarantees that a code redeploy, process crash, or SQLite lock glitch cannot
silently erase past inspections — rows can be replayed back into the DB.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

logger = logging.getLogger(__name__)

MIRROR_NAME = "inspections_mirror.jsonl"


def storage_root() -> Path:
    try:
        from app.config import get_settings

        return Path(get_settings().storage_root).expanduser().resolve()
    except Exception:
        return Path("storage").resolve()


def mirror_path(root: Path | None = None) -> Path:
    return (root or storage_root()) / MIRROR_NAME


def append_inspection_mirror(inspection_data: dict, root: Path | None = None) -> Path:
    """
    Append one inspection snapshot as a single JSON line (atomic append).

    Never overwrites prior lines. Safe across restarts on a mounted volume.
    """
    root = root or storage_root()
    root.mkdir(parents=True, exist_ok=True)
    path = mirror_path(root)
    record = {
        "mirrored_at": datetime.now(timezone.utc).isoformat(),
        "inspection": inspection_data,
    }
    line = json.dumps(record, default=str, ensure_ascii=False) + "\n"

    # Append with fsync so a crash mid-write cannot truncate the file silently.
    flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND
    if hasattr(os, "O_BINARY"):
        flags |= os.O_BINARY
    fd = os.open(str(path), flags, 0o644)
    try:
        os.write(fd, line.encode("utf-8"))
        os.fsync(fd)
    finally:
        os.close(fd)
    return path


def iter_mirror(root: Path | None = None) -> Iterator[dict]:
    """Yield the latest dict per inspection_id from the JSONL mirror (last write wins)."""
    path = mirror_path(root)
    if not path.is_file():
        return
    latest: dict[str, dict] = {}
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                logger.warning("Skipping corrupt mirror line")
                continue
            insp = row.get("inspection") or row
            iid = insp.get("inspection_id")
            if iid:
                latest[iid] = insp
    for insp in latest.values():
        yield insp


def recover_missing_into_db(root: Path | None = None, db_path: str | Path | None = None) -> int:
    """
    Re-import mirror rows that are absent from SQLite (e.g. after a partial write).

    Returns the number of inspections restored.
    """
    from app.storage.database import get_inspection, init_database, save_inspection

    root = root or storage_root()
    init_database(db_path)
    restored = 0
    for insp in iter_mirror(root):
        order = insp.get("order") or {}
        org_id = order.get("org_id")
        iid = insp.get("inspection_id")
        if not iid or not org_id:
            continue
        if get_inspection(iid, org_id, db_path) is not None:
            continue
        try:
            save_inspection(insp, db_path)
            restored += 1
            logger.warning("Recovered inspection %s for org %s from JSONL mirror", iid, org_id)
        except Exception as e:
            logger.error("Failed to recover %s from mirror: %s", iid, e)
    return restored


def write_storage_marker(root: Path | None = None) -> None:
    """Touch a marker file so operators can confirm the durable volume is mounted."""
    root = root or storage_root()
    root.mkdir(parents=True, exist_ok=True)
    marker = root / ".durable_volume"
    marker.write_text(
        f"pack-manager durable storage\nupdated={datetime.now(timezone.utc).isoformat()}\n",
        encoding="utf-8",
    )


def atomic_write_text(path: Path, text: str) -> None:
    """Write via temp file + replace for crash-safe config/marker updates."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp-", suffix=".txt")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            try:
                os.unlink(tmp)
            except OSError:
                pass
