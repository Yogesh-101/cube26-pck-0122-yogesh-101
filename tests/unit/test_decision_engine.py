"""
Unit tests for the deterministic decision engine.

Covers all official PCK test scenarios:
  1. Correct order → SEAL
  2. Missing item → STOP_AND_FIX
  3. Wrong item → STOP_AND_FIX
  4. Extra item → STOP_AND_FIX
  5. Wrong quantity → STOP_AND_FIX
  6. Multiple identical products → SEAL
  7. Visually similar products (low confidence) → UNCERTAIN
  8. Ambiguous photographs → UNCERTAIN
"""

import pytest

from app.decision.engine import DecisionResult, run_decision_engine
from app.domain.schemas import (
    CheckKey,
    Decision,
    InspectionStatus,
    ObservedItem,
    OrderLine,
    Verdict,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_order_lines(*items: tuple[str, int]) -> list[OrderLine]:
    return [OrderLine(sku=sku, quantity=qty) for sku, qty in items]


def make_observed(*items: tuple[str | None, str, int, float]) -> list[ObservedItem]:
    return [
        ObservedItem(
            sku=sku,
            name=name,
            observed_quantity=qty,
            confidence=conf,
        )
        for sku, name, qty, conf in items
    ]


def get_check(result: DecisionResult, key: CheckKey):
    return next((c for c in result.checks if c.check_key == key), None)


# ---------------------------------------------------------------------------
# Scenario 1: Correct order → SEAL
# ---------------------------------------------------------------------------

class TestCorrectOrder:
    def test_single_item_correct(self):
        result = run_decision_engine(
            expected_lines=make_order_lines(("SKU-001", 1)),
            observed_items=make_observed(("SKU-001", "Black T-Shirt", 1, 0.95)),
        )
        assert result.decision == Decision.SEAL
        assert result.status == InspectionStatus.COMPLETED
        assert len(result.discrepancies) == 0
        assert all(c.verdict == Verdict.PASS_ for c in result.checks)

    def test_multi_item_correct(self):
        result = run_decision_engine(
            expected_lines=make_order_lines(("SKU-001", 2), ("SKU-002", 1)),
            observed_items=make_observed(
                ("SKU-001", "Black T-Shirt", 2, 0.95),
                ("SKU-002", "Blue Cap", 1, 0.90),
            ),
        )
        assert result.decision == Decision.SEAL
        assert len(result.discrepancies) == 0


# ---------------------------------------------------------------------------
# Scenario 2: Missing item → STOP_AND_FIX
# ---------------------------------------------------------------------------

class TestMissingItem:
    def test_one_missing(self):
        result = run_decision_engine(
            expected_lines=make_order_lines(("SKU-001", 2), ("SKU-002", 1)),
            observed_items=make_observed(("SKU-001", "Black T-Shirt", 2, 0.95)),
        )
        assert result.decision == Decision.STOP_AND_FIX
        items_check = get_check(result, CheckKey.ITEMS_PRESENT)
        assert items_check is not None
        assert items_check.verdict == Verdict.FAIL
        assert any(d.discrepancy_type == "missing" for d in result.discrepancies)

    def test_all_missing(self):
        result = run_decision_engine(
            expected_lines=make_order_lines(("SKU-001", 1)),
            observed_items=make_observed(),
        )
        assert result.decision == Decision.STOP_AND_FIX
        items_check = get_check(result, CheckKey.ITEMS_PRESENT)
        assert items_check.verdict == Verdict.FAIL


# ---------------------------------------------------------------------------
# Scenario 3: Wrong item → STOP_AND_FIX
# ---------------------------------------------------------------------------

class TestWrongItem:
    def test_wrong_sku_instead_of_expected(self):
        """Expected Blue Cap, got Red Cap (different SKU)."""
        result = run_decision_engine(
            expected_lines=make_order_lines(("SKU-BLUE-CAP", 1)),
            observed_items=make_observed(("SKU-RED-CAP", "Red Cap", 1, 0.95)),
        )
        assert result.decision == Decision.STOP_AND_FIX
        # SKU-BLUE-CAP missing + SKU-RED-CAP extra
        items_check = get_check(result, CheckKey.ITEMS_PRESENT)
        assert items_check.verdict == Verdict.FAIL
        extra_check = get_check(result, CheckKey.NO_EXTRA_ITEMS)
        assert extra_check.verdict == Verdict.FAIL

    def test_wrong_plus_correct(self):
        """Order has 2 items, one correct and one swapped."""
        result = run_decision_engine(
            expected_lines=make_order_lines(("SKU-001", 1), ("SKU-002", 1)),
            observed_items=make_observed(
                ("SKU-001", "Black T-Shirt", 1, 0.95),
                ("SKU-003", "Red Socks", 1, 0.90),
            ),
        )
        assert result.decision == Decision.STOP_AND_FIX


# ---------------------------------------------------------------------------
# Scenario 4: Extra item → STOP_AND_FIX
# ---------------------------------------------------------------------------

class TestExtraItem:
    def test_extra_known_sku(self):
        result = run_decision_engine(
            expected_lines=make_order_lines(("SKU-001", 1)),
            observed_items=make_observed(
                ("SKU-001", "Black T-Shirt", 1, 0.95),
                ("SKU-999", "Random Widget", 1, 0.90),
            ),
        )
        assert result.decision == Decision.STOP_AND_FIX
        extra_check = get_check(result, CheckKey.NO_EXTRA_ITEMS)
        assert extra_check.verdict == Verdict.FAIL
        assert any(d.discrepancy_type == "extra" for d in result.discrepancies)

    def test_extra_unknown_item(self):
        """Unknown item (no SKU) found in the package."""
        result = run_decision_engine(
            expected_lines=make_order_lines(("SKU-001", 1)),
            observed_items=make_observed(
                ("SKU-001", "Black T-Shirt", 1, 0.95),
                (None, "Mystery Object", 1, 0.85),
            ),
        )
        assert result.decision == Decision.STOP_AND_FIX
        assert any(d.discrepancy_type == "unknown_item" for d in result.discrepancies)


# ---------------------------------------------------------------------------
# Scenario 5: Wrong quantity → STOP_AND_FIX
# ---------------------------------------------------------------------------

class TestWrongQuantity:
    def test_too_few(self):
        result = run_decision_engine(
            expected_lines=make_order_lines(("SKU-001", 3)),
            observed_items=make_observed(("SKU-001", "Black T-Shirt", 2, 0.95)),
        )
        assert result.decision == Decision.STOP_AND_FIX
        qty_check = get_check(result, CheckKey.QUANTITY_MATCH)
        assert qty_check.verdict == Verdict.FAIL

    def test_too_many(self):
        result = run_decision_engine(
            expected_lines=make_order_lines(("SKU-001", 1)),
            observed_items=make_observed(("SKU-001", "Black T-Shirt", 3, 0.95)),
        )
        assert result.decision == Decision.STOP_AND_FIX
        qty_check = get_check(result, CheckKey.QUANTITY_MATCH)
        assert qty_check.verdict == Verdict.FAIL
        assert any(d.discrepancy_type == "wrong_quantity" for d in result.discrepancies)


# ---------------------------------------------------------------------------
# Scenario 6: Multiple identical products → SEAL
# ---------------------------------------------------------------------------

class TestMultipleIdentical:
    def test_five_of_same(self):
        result = run_decision_engine(
            expected_lines=make_order_lines(("SKU-001", 5)),
            observed_items=make_observed(("SKU-001", "Black T-Shirt", 5, 0.90)),
        )
        assert result.decision == Decision.SEAL
        assert len(result.discrepancies) == 0

    def test_multiple_skus_multiple_quantities(self):
        result = run_decision_engine(
            expected_lines=make_order_lines(("SKU-001", 3), ("SKU-002", 2)),
            observed_items=make_observed(
                ("SKU-001", "Black T-Shirt", 3, 0.90),
                ("SKU-002", "Blue Cap", 2, 0.88),
            ),
        )
        assert result.decision == Decision.SEAL


# ---------------------------------------------------------------------------
# Scenario 7: Visually similar products (low confidence) → UNCERTAIN
# ---------------------------------------------------------------------------

class TestVisuallySimilar:
    def test_low_confidence_identification(self):
        """VLM returns correct SKU but with low confidence (< 0.5)."""
        result = run_decision_engine(
            expected_lines=make_order_lines(("SKU-001", 1)),
            observed_items=make_observed(("SKU-001", "Black T-Shirt", 1, 0.3)),
        )
        assert result.decision == Decision.STOP_AND_FIX
        assert result.status == InspectionStatus.PENDING_REVIEW
        assert len(result.uncertainties) > 0


# ---------------------------------------------------------------------------
# Scenario 8: Ambiguous photographs → UNCERTAIN
# ---------------------------------------------------------------------------

class TestAmbiguousPhotos:
    def test_bad_image_quality(self):
        result = run_decision_engine(
            expected_lines=make_order_lines(("SKU-001", 1)),
            observed_items=make_observed(("SKU-001", "Black T-Shirt", 1, 0.95)),
            image_quality_ok=False,
        )
        assert result.decision == Decision.STOP_AND_FIX
        iq_check = get_check(result, CheckKey.IMAGE_QUALITY)
        assert iq_check.verdict == Verdict.FAIL

    def test_uncertain_image_quality(self):
        result = run_decision_engine(
            expected_lines=make_order_lines(("SKU-001", 1)),
            observed_items=make_observed(("SKU-001", "Black T-Shirt", 1, 0.95)),
            image_quality_uncertain=True,
        )
        assert result.decision == Decision.STOP_AND_FIX
        assert result.status == InspectionStatus.PENDING_REVIEW
        iq_check = get_check(result, CheckKey.IMAGE_QUALITY)
        assert iq_check.verdict == Verdict.UNCERTAIN


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_empty_observed_with_expected(self):
        """Nothing observed but items expected."""
        result = run_decision_engine(
            expected_lines=make_order_lines(("SKU-001", 1)),
            observed_items=[],
        )
        assert result.decision == Decision.STOP_AND_FIX
        assert any(d.discrepancy_type == "missing" for d in result.discrepancies)

    def test_fail_overrides_uncertain(self):
        """A FAIL check should produce STOP_AND_FIX even if uncertain checks exist."""
        result = run_decision_engine(
            expected_lines=make_order_lines(("SKU-001", 1), ("SKU-002", 1)),
            observed_items=make_observed(
                ("SKU-001", "Black T-Shirt", 1, 0.3),  # low confidence → uncertain
            ),
            # SKU-002 is missing → FAIL
        )
        assert result.decision == Decision.STOP_AND_FIX
        assert result.status == InspectionStatus.COMPLETED  # FAIL overrides review

    def test_model_version_propagated(self):
        result = run_decision_engine(
            expected_lines=make_order_lines(("SKU-001", 1)),
            observed_items=make_observed(("SKU-001", "Black T-Shirt", 1, 0.95)),
            model_version="gemini-2.5-flash-001",
            latency_ms=1234.5,
        )
        for check in result.checks:
            assert check.model_version == "gemini-2.5-flash-001"

    def test_no_wrong_items_check_exists(self):
        result = run_decision_engine(
            expected_lines=make_order_lines(("SKU-001", 1)),
            observed_items=make_observed(("SKU-001", "Black T-Shirt", 1, 0.95)),
        )
        wrong_check = get_check(result, CheckKey.NO_WRONG_ITEMS)
        assert wrong_check is not None
        assert wrong_check.verdict == Verdict.PASS_

    def test_duplicate_observed_skus_aggregated(self):
        """Multiple observations of the same SKU should be aggregated."""
        result = run_decision_engine(
            expected_lines=make_order_lines(("SKU-001", 3)),
            observed_items=make_observed(
                ("SKU-001", "Black T-Shirt", 2, 0.90),
                ("SKU-001", "Black T-Shirt", 1, 0.85),
            ),
        )
        assert result.decision == Decision.SEAL

    def test_unknown_low_confidence_extras_are_uncertain(self):
        """Unknown items with confidence < 0.5 should mark UNCERTAIN, not FAIL."""
        result = run_decision_engine(
            expected_lines=make_order_lines(("SKU-001", 1)),
            observed_items=make_observed(
                ("SKU-001", "Black T-Shirt", 1, 0.95),
                (None, "Maybe Shadow", 1, 0.2),
            ),
        )
        extra_check = get_check(result, CheckKey.NO_EXTRA_ITEMS)
        assert extra_check.verdict == Verdict.UNCERTAIN
        assert result.status == InspectionStatus.PENDING_REVIEW
