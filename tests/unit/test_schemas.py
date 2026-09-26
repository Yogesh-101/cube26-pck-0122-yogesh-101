"""
Unit tests for Pydantic data contracts.

Validates that schemas enforce the constraints required by
the official evidence contract and engineering rules.
"""

import json

import pytest

from app.domain.schemas import (
    Channel,
    Check,
    CheckKey,
    Decision,
    EvidenceRecord,
    HumanOverride,
    ImageInput,
    InspectionStatus,
    ObservedItem,
    Order,
    OrderLine,
    Outcome,
    Verdict,
)


class TestOrderValidation:
    def test_valid_order(self):
        order = Order(
            order_id="ORD-001",
            unit_id="UNIT-0001",
            org_id="org_demo_alpha",
            channel=Channel.AMAZON_MFN,
            lines=[OrderLine(sku="SKU-001", quantity=2)],
        )
        assert order.order_id == "ORD-001"
        assert order.channel == Channel.AMAZON_MFN

    def test_fba_channel_rejected(self):
        with pytest.raises(ValueError, match="FBA"):
            Order(
                order_id="ORD-001",
                unit_id="UNIT-0001",
                org_id="org_demo_alpha",
                channel="fba",
                lines=[OrderLine(sku="SKU-001", quantity=1)],
            )

    def test_amazon_fba_rejected(self):
        with pytest.raises(ValueError, match="FBA"):
            Order(
                order_id="ORD-001",
                unit_id="UNIT-0001",
                org_id="org_demo_alpha",
                channel="amazon_fba",
                lines=[OrderLine(sku="SKU-001", quantity=1)],
            )

    def test_empty_lines_rejected(self):
        with pytest.raises(ValueError):
            Order(
                order_id="ORD-001",
                unit_id="UNIT-0001",
                org_id="org_demo_alpha",
                channel=Channel.SHOPIFY,
                lines=[],
            )

    def test_all_valid_channels(self):
        for ch in Channel:
            order = Order(
                order_id="ORD-001",
                unit_id="UNIT-0001",
                org_id="org_demo_alpha",
                channel=ch,
                lines=[OrderLine(sku="SKU-001", quantity=1)],
            )
            assert order.channel == ch

    def test_zero_quantity_rejected(self):
        with pytest.raises(ValueError):
            OrderLine(sku="SKU-001", quantity=0)

    def test_negative_quantity_rejected(self):
        with pytest.raises(ValueError):
            OrderLine(sku="SKU-001", quantity=-1)


class TestEvidenceRecord:
    def test_record_id_prefix(self):
        record = EvidenceRecord(
            organization_id="org_demo_alpha",
            subject="UNIT-0001",
        )
        assert record.record_id.startswith("PCK-")

    def test_content_hash_deterministic(self):
        record = EvidenceRecord(
            organization_id="org_demo_alpha",
            subject="UNIT-0001",
        )
        hash1 = record.compute_content_hash()
        hash2 = record.compute_content_hash()
        assert hash1 == hash2
        assert len(hash1) == 64  # SHA-256 hex

    def test_content_hash_changes_on_data_change(self):
        r1 = EvidenceRecord(
            organization_id="org_demo_alpha",
            subject="UNIT-0001",
        )
        r2 = EvidenceRecord(
            organization_id="org_demo_bravo",
            subject="UNIT-0001",
        )
        assert r1.compute_content_hash() != r2.compute_content_hash()

    def test_finalize_sets_hash(self):
        record = EvidenceRecord(
            organization_id="org_demo_alpha",
            subject="UNIT-0001",
        )
        assert record.content_hash is None
        record.finalize()
        assert record.content_hash is not None
        assert len(record.content_hash) == 64

    def test_schema_version_set(self):
        record = EvidenceRecord(
            organization_id="org_demo_alpha",
            subject="UNIT-0001",
        )
        assert record.schema_version == "1.0.0"

    def test_agent_field(self):
        record = EvidenceRecord(
            organization_id="org_demo_alpha",
            subject="UNIT-0001",
        )
        assert record.agent == "pack_manager"

    def test_serialization_roundtrip(self):
        record = EvidenceRecord(
            organization_id="org_demo_alpha",
            subject="UNIT-0001",
            checks=[
                Check(
                    check_key=CheckKey.ITEMS_PRESENT,
                    verdict=Verdict.PASS_,
                    confidence=0.95,
                    detail="All items found",
                    model_version="gemini-2.5-flash",
                    latency_ms=1200.0,
                )
            ],
            outcome=Outcome(decision=Decision.SEAL),
            status=InspectionStatus.COMPLETED,
        )
        record.finalize()
        data = record.model_dump(mode="json")
        restored = EvidenceRecord.model_validate(data)
        assert restored.content_hash == record.content_hash
        assert restored.checks[0].verdict == Verdict.PASS_


class TestHumanOverride:
    def test_override_preserves_original(self):
        override = HumanOverride(
            original_decision=Decision.STOP_AND_FIX,
            new_decision=Decision.SEAL,
            reason="Operator confirmed item was correct variant",
            operator_id="OP-001",
        )
        assert override.original_decision == Decision.STOP_AND_FIX
        assert override.new_decision == Decision.SEAL

    def test_override_requires_reason(self):
        with pytest.raises(ValueError):
            HumanOverride(
                original_decision=Decision.SEAL,
                new_decision=Decision.STOP_AND_FIX,
                reason="",
                operator_id="OP-001",
            )


class TestObservedItem:
    def test_confidence_bounds(self):
        with pytest.raises(ValueError):
            ObservedItem(
                sku="SKU-001", name="Test", observed_quantity=1, confidence=1.5
            )
        with pytest.raises(ValueError):
            ObservedItem(
                sku="SKU-001", name="Test", observed_quantity=1, confidence=-0.1
            )

    def test_null_sku_allowed(self):
        item = ObservedItem(
            sku=None, name="Unknown Item", observed_quantity=1, confidence=0.3
        )
        assert item.sku is None
