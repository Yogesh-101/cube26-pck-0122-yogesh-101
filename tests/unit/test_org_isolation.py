"""
Tests for tenancy isolation (Engineering Rule 1).

Rule: Every query is scoped to org_id. A second org must see zero rows
and must not be able to fetch another org's data by guessing a key.
"""

import os
import tempfile

import pytest

from app.storage.database import (
    get_inspection,
    get_overrides,
    init_database,
    list_inspections,
    save_inspection,
    save_override,
)


@pytest.fixture
def db_path():
    """Create a temporary database for each test."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    init_database(path)
    yield path
    os.unlink(path)


def _make_inspection(inspection_id: str, org_id: str, order_id: str = "ORD-001") -> dict:
    return {
        "inspection_id": inspection_id,
        "order": {
            "order_id": order_id,
            "unit_id": "UNIT-0001",
            "org_id": org_id,
            "channel": "amazon_mfn",
            "lines": [{"sku": "SKU-001", "quantity": 1}],
        },
        "observed_items": [{"sku": "SKU-001", "name": "Test", "observed_quantity": 1, "confidence": 0.9}],
        "checks": [{"check_key": "items_present", "verdict": "pass", "confidence": 0.95, "detail": "OK"}],
        "outcome": {"decision": "seal", "decided_by": "engine", "decided_at": "2026-09-26T12:00:00Z"},
        "status": "completed",
        "evidence_record": {"record_id": f"PCK-{inspection_id}", "organization_id": org_id, "content_hash": "abc123"},
    }


class TestOrgIsolation:
    """
    Verifies that org_demo_alpha cannot see org_demo_bravo's data,
    and vice versa — even by guessing inspection IDs.
    """

    def test_alpha_cannot_see_bravo_inspections(self, db_path):
        """org_demo_alpha must see zero rows from org_demo_bravo."""
        save_inspection(_make_inspection("INS-001", "org_demo_alpha"), db_path)
        save_inspection(_make_inspection("INS-002", "org_demo_bravo"), db_path)

        alpha_list = list_inspections("org_demo_alpha", db_path)
        bravo_list = list_inspections("org_demo_bravo", db_path)

        assert len(alpha_list) == 1
        assert alpha_list[0]["inspection_id"] == "INS-001"

        assert len(bravo_list) == 1
        assert bravo_list[0]["inspection_id"] == "INS-002"

    def test_cannot_fetch_other_org_by_guessing_id(self, db_path):
        """org_demo_bravo must not fetch org_demo_alpha's inspection by ID."""
        save_inspection(_make_inspection("INS-001", "org_demo_alpha"), db_path)

        # Alpha can see it
        result = get_inspection("INS-001", "org_demo_alpha", db_path)
        assert result is not None

        # Bravo cannot, even with the correct ID
        result = get_inspection("INS-001", "org_demo_bravo", db_path)
        assert result is None

    def test_override_scoped_to_org(self, db_path):
        """Overrides are scoped to the org that owns the inspection."""
        save_inspection(_make_inspection("INS-001", "org_demo_alpha"), db_path)

        # Alpha can override
        success = save_override(
            "INS-001", "org_demo_alpha", "seal", "stop_and_fix", "Wrong item found", "OP-001", db_path
        )
        assert success is True

        # Bravo cannot override alpha's inspection
        success = save_override(
            "INS-001", "org_demo_bravo", "seal", "stop_and_fix", "Attempted override", "OP-002", db_path
        )
        assert success is False

    def test_overrides_not_visible_to_other_org(self, db_path):
        """Overrides for alpha's inspection must not be visible to bravo."""
        save_inspection(_make_inspection("INS-001", "org_demo_alpha"), db_path)
        save_override("INS-001", "org_demo_alpha", "seal", "stop_and_fix", "Reason", "OP-001", db_path)

        alpha_overrides = get_overrides("INS-001", "org_demo_alpha", db_path)
        bravo_overrides = get_overrides("INS-001", "org_demo_bravo", db_path)

        assert len(alpha_overrides) == 1
        assert len(bravo_overrides) == 0

    def test_empty_org_sees_nothing(self, db_path):
        """An org with no inspections must see zero rows."""
        save_inspection(_make_inspection("INS-001", "org_demo_alpha"), db_path)

        result = list_inspections("org_nonexistent", db_path)
        assert result == []

    def test_multiple_inspections_per_org(self, db_path):
        """Each org only sees its own inspections."""
        for i in range(5):
            save_inspection(_make_inspection(f"INS-A{i}", "org_demo_alpha", f"ORD-A{i}"), db_path)
        for i in range(3):
            save_inspection(_make_inspection(f"INS-B{i}", "org_demo_bravo", f"ORD-B{i}"), db_path)

        alpha = list_inspections("org_demo_alpha", db_path)
        bravo = list_inspections("org_demo_bravo", db_path)

        assert len(alpha) == 5
        assert len(bravo) == 3
        assert all(i["inspection_id"].startswith("INS-A") for i in alpha)
        assert all(i["inspection_id"].startswith("INS-B") for i in bravo)
