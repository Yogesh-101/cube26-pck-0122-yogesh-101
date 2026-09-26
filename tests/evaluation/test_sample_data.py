"""
Evaluation against the official pack_sample.csv (29 units).

This tests the decision engine against the organiser's ground truth data.
The CSV contains deliberately wrong operator verdicts — our engine must
produce the CORRECT decision independently (not copy the operator).
"""

import pytest

from app.data_loader import load_sample_data
from app.decision.engine import run_decision_engine
from app.domain.schemas import Decision


class TestSampleDataDecisions:
    """Run the decision engine against all 29 CSV rows."""

    @pytest.fixture(scope="class")
    def sample_records(self):
        return load_sample_data()

    def test_csv_loaded(self, sample_records):
        assert len(sample_records) == 29, f"Expected 29 rows, got {len(sample_records)}"

    def test_all_decisions_correct(self, sample_records):
        """Every decision must match ground truth (expected vs observed)."""
        failures = []
        for rec in sample_records:
            result = run_decision_engine(
                expected_lines=rec["expected_lines"],
                observed_items=rec["observed_items"],
            )
            expected = Decision(rec["ground_truth_decision"])
            if result.decision != expected:
                failures.append(
                    f"{rec['record_id']}: expected {expected.value}, got {result.decision.value} "
                    f"(type: {rec['discrepancy_type']})"
                )
        assert not failures, f"Decision failures:\n" + "\n".join(failures)

    def test_zero_false_pass(self, sample_records):
        """CRITICAL: never approve a package that should be stopped."""
        false_passes = []
        for rec in sample_records:
            if rec["ground_truth_decision"] != "stop_and_fix":
                continue
            result = run_decision_engine(
                expected_lines=rec["expected_lines"],
                observed_items=rec["observed_items"],
            )
            if result.decision == Decision.SEAL:
                false_passes.append(rec["record_id"])
        assert not false_passes, f"FALSE PASS on: {false_passes}"

    def test_engine_independent_of_operator(self, sample_records):
        """
        The CSV contains deliberately wrong operator verdicts.
        Our engine must NOT copy the operator — it must reason independently.
        """
        wrong_operators = [r for r in sample_records if not r["operator_correct"]]
        assert len(wrong_operators) > 0, "Expected some deliberately wrong operator verdicts"

        for rec in wrong_operators:
            result = run_decision_engine(
                expected_lines=rec["expected_lines"],
                observed_items=rec["observed_items"],
            )
            expected = Decision(rec["ground_truth_decision"])
            assert result.decision == expected, (
                f"{rec['record_id']}: operator said '{rec['operator_verdict']}' (wrong), "
                f"ground truth is '{expected.value}', engine said '{result.decision.value}'"
            )

    def test_discrepancy_types_detected(self, sample_records):
        """Verify the engine catches each type of discrepancy."""
        types_found = set()
        for rec in sample_records:
            if rec["discrepancy_type"] != "correct":
                result = run_decision_engine(
                    expected_lines=rec["expected_lines"],
                    observed_items=rec["observed_items"],
                )
                if result.decision == Decision.STOP_AND_FIX:
                    types_found.add(rec["discrepancy_type"])

        # The CSV has extra_item cases (UNIT-0027, UNIT-0034, UNIT-0078)
        # and wrong_item cases (UNIT-0044)
        assert "extra_item" in types_found, "Should detect extra items"

    def test_correct_orders_sealed(self, sample_records):
        """All correct orders should be SEAL."""
        correct = [r for r in sample_records if r["discrepancy_type"] == "correct"]
        assert len(correct) > 0

        for rec in correct:
            result = run_decision_engine(
                expected_lines=rec["expected_lines"],
                observed_items=rec["observed_items"],
            )
            assert result.decision == Decision.SEAL, (
                f"{rec['record_id']}: correct order should be SEAL, got {result.decision.value}"
            )

    def test_org_distribution(self, sample_records):
        """Verify both orgs are represented (for isolation testing)."""
        orgs = {r["org_id"] for r in sample_records}
        assert "org_demo_alpha" in orgs
        assert "org_demo_bravo" in orgs
