"""
Pytest wrapper for the evaluation harness.

Ensures the decision engine achieves 100% on the deterministic eval dataset
with zero false-PASS incidents.
"""

from tests.evaluation.eval_harness import build_eval_dataset, run_evaluation


class TestEvalHarness:
    def test_all_scenarios_pass(self):
        cases = build_eval_dataset()
        metrics = run_evaluation(cases)

        assert metrics.total >= 50, f"Need ≥50 eval cases, got {metrics.total}"
        assert metrics.correct_decisions == metrics.total, (
            f"{metrics.total - metrics.correct_decisions} decision failures: "
            f"{[f['case_id'] for f in metrics.failures]}"
        )

    def test_zero_false_pass(self):
        """False PASS (approving an incorrect package) is the headline safety metric."""
        cases = build_eval_dataset()
        metrics = run_evaluation(cases)
        assert metrics.false_pass == 0, f"CRITICAL: {metrics.false_pass} false PASS incidents"

    def test_uncertain_routes_to_review(self):
        """UNCERTAIN verdicts must produce pending_review, never auto-seal."""
        cases = build_eval_dataset()
        metrics = run_evaluation(cases)
        assert metrics.pending_review_count >= 5, (
            f"Expected ≥5 pending_review cases, got {metrics.pending_review_count}"
        )
