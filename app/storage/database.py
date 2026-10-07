"""
PCK Pack Manager — SQLite Persistence Layer

Stores inspection records and evidence with org-level tenancy isolation.
Engineering Rule 1: Every query is scoped to org_id.
Engineering Rule 3: Fail-open — errors in storage never block the operator.

The database file lives under STORAGE_ROOT (default ./storage/pack_manager.db)
so a single mounted volume keeps inspections + photos across restarts.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


def get_db_path() -> Path:
    """Resolved SQLite path from settings (always under the durable storage root)."""
    try:
        from app.config import get_settings

        return Path(get_settings().database_path).expanduser().resolve()
    except Exception:
        return Path("storage/pack_manager.db").resolve()


# Back-compat alias used by a few call sites
DB_PATH = Path("storage/pack_manager.db")


def _get_connection(db_path: str | Path | None = None) -> sqlite3.Connection:
    """Get a SQLite connection with WAL mode for concurrency."""
    path = Path(db_path) if db_path is not None else get_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.row_factory = sqlite3.Row
    return conn


def init_database(db_path: str | Path | None = None) -> None:
    """Initialize the database schema (idempotent)."""
    path = Path(db_path) if db_path is not None else get_db_path()
    conn = _get_connection(path)
    try:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS inspections (
                inspection_id TEXT PRIMARY KEY,
                order_id TEXT NOT NULL,
                unit_id TEXT NOT NULL,
                org_id TEXT NOT NULL,
                channel TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                decision TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                order_data TEXT NOT NULL,
                observed_items TEXT,
                checks TEXT,
                outcome TEXT,
                evidence_record TEXT,
                catalogue TEXT,
                images TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_inspections_org
                ON inspections(org_id);

            CREATE INDEX IF NOT EXISTS idx_inspections_unit
                ON inspections(unit_id);

            CREATE INDEX IF NOT EXISTS idx_inspections_order
                ON inspections(order_id);

            CREATE TABLE IF NOT EXISTS overrides (
                override_id INTEGER PRIMARY KEY AUTOINCREMENT,
                inspection_id TEXT NOT NULL,
                org_id TEXT NOT NULL,
                original_decision TEXT NOT NULL,
                new_decision TEXT NOT NULL,
                reason TEXT NOT NULL,
                operator_id TEXT NOT NULL,
                overridden_at TEXT NOT NULL,
                FOREIGN KEY (inspection_id) REFERENCES inspections(inspection_id)
            );

            CREATE INDEX IF NOT EXISTS idx_overrides_inspection
                ON overrides(inspection_id);

            CREATE INDEX IF NOT EXISTS idx_overrides_org
                ON overrides(org_id);
        """)
        # Migrate older DBs that pre-date the images column
        cols = {row[1] for row in conn.execute("PRAGMA table_info(inspections)").fetchall()}
        if "images" not in cols:
            conn.execute("ALTER TABLE inspections ADD COLUMN images TEXT")
        conn.commit()
        logger.info("Database initialized at %s", path)
    finally:
        conn.close()


def save_inspection(inspection_data: dict, db_path: str | Path | None = None) -> None:
    """
    Upsert an inspection record. Preserves the original created_at.
    All queries are org-scoped via the stored org_id column (Rule 1).
    """
    path = Path(db_path) if db_path is not None else get_db_path()
    conn = _get_connection(path)
    try:
        order = inspection_data.get("order", {})
        now = datetime.now(timezone.utc).isoformat()
        inspection_id = inspection_data.get("inspection_id")

        outcome = inspection_data.get("outcome")
        decision = outcome.get("decision") if outcome else None

        existing = conn.execute(
            "SELECT created_at FROM inspections WHERE inspection_id = ?",
            (inspection_id,),
        ).fetchone()
        created_at = existing["created_at"] if existing else now

        conn.execute("""
            INSERT INTO inspections
            (inspection_id, order_id, unit_id, org_id, channel, status, decision,
             created_at, updated_at, order_data, observed_items, checks, outcome,
             evidence_record, catalogue, images)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(inspection_id) DO UPDATE SET
                order_id=excluded.order_id,
                unit_id=excluded.unit_id,
                org_id=excluded.org_id,
                channel=excluded.channel,
                status=excluded.status,
                decision=excluded.decision,
                updated_at=excluded.updated_at,
                order_data=excluded.order_data,
                observed_items=excluded.observed_items,
                checks=excluded.checks,
                outcome=excluded.outcome,
                evidence_record=excluded.evidence_record,
                catalogue=excluded.catalogue,
                images=excluded.images
        """, (
            inspection_id,
            order.get("order_id"),
            order.get("unit_id"),
            order.get("org_id"),
            order.get("channel"),
            inspection_data.get("status", "pending"),
            decision,
            created_at,
            now,
            json.dumps(order),
            json.dumps(inspection_data.get("observed_items", [])),
            json.dumps(inspection_data.get("checks", [])),
            json.dumps(outcome) if outcome else None,
            json.dumps(inspection_data.get("evidence_record")) if inspection_data.get("evidence_record") else None,
            json.dumps(inspection_data.get("catalogue", [])),
            json.dumps(inspection_data.get("images", [])),
        ))
        conn.commit()
    finally:
        conn.close()


def get_inspection(inspection_id: str, org_id: str, db_path: str | Path | None = None) -> Optional[dict]:
    """
    Retrieve a single inspection. Scoped to org_id (Rule 1).
    Returns None if not found or wrong org.
    """
    path = Path(db_path) if db_path is not None else get_db_path()
    conn = _get_connection(path)
    try:
        row = conn.execute(
            "SELECT * FROM inspections WHERE inspection_id = ? AND org_id = ?",
            (inspection_id, org_id),
        ).fetchone()

        if not row:
            return None

        return _row_to_dict(row)
    finally:
        conn.close()


def list_inspections(org_id: str, db_path: str | Path | None = None) -> list[dict]:
    """
    List all inspections for an org. Scoped to org_id (Rule 1).
    """
    path = Path(db_path) if db_path is not None else get_db_path()
    conn = _get_connection(path)
    try:
        rows = conn.execute(
            "SELECT inspection_id, order_id, unit_id, status, decision, created_at "
            "FROM inspections WHERE org_id = ? ORDER BY created_at DESC",
            (org_id,),
        ).fetchall()

        return [
            {
                "inspection_id": r["inspection_id"],
                "order_id": r["order_id"],
                "unit_id": r["unit_id"],
                "status": r["status"],
                "decision": r["decision"],
                "created_at": r["created_at"],
            }
            for r in rows
        ]
    finally:
        conn.close()


def save_override(
    inspection_id: str,
    org_id: str,
    original_decision: str,
    new_decision: str,
    reason: str,
    operator_id: str,
    db_path: str | Path | None = None,
) -> bool:
    """
    Save a human override. Append-only, never overwrites original.
    Returns True on success.
    """
    path = Path(db_path) if db_path is not None else get_db_path()
    conn = _get_connection(path)
    try:
        row = conn.execute(
            "SELECT inspection_id FROM inspections WHERE inspection_id = ? AND org_id = ?",
            (inspection_id, org_id),
        ).fetchone()
        if not row:
            return False

        now = datetime.now(timezone.utc).isoformat()

        conn.execute("""
            INSERT INTO overrides
            (inspection_id, org_id, original_decision, new_decision, reason, operator_id, overridden_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (inspection_id, org_id, original_decision, new_decision, reason, operator_id, now))

        conn.execute(
            "UPDATE inspections SET decision = ?, status = 'completed', updated_at = ? "
            "WHERE inspection_id = ? AND org_id = ?",
            (new_decision, now, inspection_id, org_id),
        )

        conn.commit()
        return True
    finally:
        conn.close()


def get_overrides(inspection_id: str, org_id: str, db_path: str | Path | None = None) -> list[dict]:
    """Get all overrides for an inspection. Scoped to org_id."""
    path = Path(db_path) if db_path is not None else get_db_path()
    conn = _get_connection(path)
    try:
        rows = conn.execute(
            "SELECT * FROM overrides WHERE inspection_id = ? AND org_id = ? ORDER BY overridden_at",
            (inspection_id, org_id),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def _row_to_dict(row: sqlite3.Row) -> dict:
    """Convert a database row back to a full inspection dict."""
    data = dict(row)

    for field in ("order_data", "observed_items", "checks", "outcome", "evidence_record", "catalogue", "images"):
        if data.get(field):
            try:
                data[field] = json.loads(data[field])
            except (json.JSONDecodeError, TypeError):
                pass

    result = {
        "inspection_id": data["inspection_id"],
        "order": data.get("order_data", {}),
        "observed_items": data.get("observed_items", []) or [],
        "checks": data.get("checks", []) or [],
        "outcome": data.get("outcome"),
        "status": data["status"],
        "evidence_record": data.get("evidence_record"),
        "catalogue": data.get("catalogue", []) or [],
        "images": data.get("images", []) or [],
    }
    if data.get("created_at"):
        result["created_at"] = data["created_at"]
    if data.get("updated_at"):
        result["updated_at"] = data["updated_at"]
    return result
