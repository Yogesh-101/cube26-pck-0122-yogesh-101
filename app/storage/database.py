"""
PCK Pack Manager — SQLite Persistence Layer

Stores inspection records and evidence with org-level tenancy isolation.
Engineering Rule 1: Every query is scoped to org_id.
Engineering Rule 3: Fail-open — errors in storage never block the operator.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

DB_PATH = Path("pack_manager.db")


def _get_connection(db_path: str | Path = DB_PATH) -> sqlite3.Connection:
    """Get a SQLite connection with WAL mode for concurrency."""
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.row_factory = sqlite3.Row
    return conn


def init_database(db_path: str | Path = DB_PATH) -> None:
    """Initialize the database schema."""
    conn = _get_connection(db_path)
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
                catalogue TEXT
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
        conn.commit()
        logger.info(f"Database initialized at {db_path}")
    finally:
        conn.close()


def save_inspection(inspection_data: dict, db_path: str | Path = DB_PATH) -> None:
    """
    Save an inspection record. All queries scoped to org_id (Rule 1).
    """
    conn = _get_connection(db_path)
    try:
        order = inspection_data.get("order", {})
        now = datetime.now(timezone.utc).isoformat()

        outcome = inspection_data.get("outcome")
        decision = outcome.get("decision") if outcome else None

        conn.execute("""
            INSERT OR REPLACE INTO inspections
            (inspection_id, order_id, unit_id, org_id, channel, status, decision,
             created_at, updated_at, order_data, observed_items, checks, outcome,
             evidence_record, catalogue)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            inspection_data.get("inspection_id"),
            order.get("order_id"),
            order.get("unit_id"),
            order.get("org_id"),
            order.get("channel"),
            inspection_data.get("status", "pending"),
            decision,
            now,
            now,
            json.dumps(order),
            json.dumps(inspection_data.get("observed_items", [])),
            json.dumps(inspection_data.get("checks", [])),
            json.dumps(outcome) if outcome else None,
            json.dumps(inspection_data.get("evidence_record")) if inspection_data.get("evidence_record") else None,
            json.dumps(inspection_data.get("catalogue", [])),
        ))
        conn.commit()
    finally:
        conn.close()


def get_inspection(inspection_id: str, org_id: str, db_path: str | Path = DB_PATH) -> Optional[dict]:
    """
    Retrieve a single inspection. Scoped to org_id (Rule 1).
    Returns None if not found or wrong org.
    """
    conn = _get_connection(db_path)
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


def list_inspections(org_id: str, db_path: str | Path = DB_PATH) -> list[dict]:
    """
    List all inspections for an org. Scoped to org_id (Rule 1).
    """
    conn = _get_connection(db_path)
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
    db_path: str | Path = DB_PATH,
) -> bool:
    """
    Save a human override. Append-only, never overwrites original.
    Returns True on success.
    """
    conn = _get_connection(db_path)
    try:
        # Verify inspection exists and belongs to org
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

        # Update inspection's decision but preserve evidence
        conn.execute(
            "UPDATE inspections SET decision = ?, status = 'completed', updated_at = ? "
            "WHERE inspection_id = ? AND org_id = ?",
            (new_decision, now, inspection_id, org_id),
        )

        conn.commit()
        return True
    finally:
        conn.close()


def get_overrides(inspection_id: str, org_id: str, db_path: str | Path = DB_PATH) -> list[dict]:
    """Get all overrides for an inspection. Scoped to org_id."""
    conn = _get_connection(db_path)
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

    # Parse JSON fields back
    for field in ("order_data", "observed_items", "checks", "outcome", "evidence_record", "catalogue"):
        if data.get(field):
            try:
                data[field] = json.loads(data[field])
            except (json.JSONDecodeError, TypeError):
                pass

    # Reshape to match our API format
    result = {
        "inspection_id": data["inspection_id"],
        "order": data.get("order_data", {}),
        "observed_items": data.get("observed_items", []),
        "checks": data.get("checks", []),
        "outcome": data.get("outcome"),
        "status": data["status"],
        "evidence_record": data.get("evidence_record"),
        "catalogue": data.get("catalogue", []),
    }
    if data.get("created_at"):
        result["created_at"] = data["created_at"]
    return result
