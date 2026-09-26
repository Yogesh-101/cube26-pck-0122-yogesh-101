"""
PCK Pack Manager — Evaluation Harness

Measures the decision engine's performance across all official test scenarios.
Produces per-check metrics: accuracy, FP, FN, UNCERTAIN rate, false-PASS rate.

Usage:
    python -m tests.evaluation.eval_harness [--output docs/EVALUATION.md]
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from app.decision.engine import run_decision_engine
from app.domain.schemas import (
    CheckKey,
    Decision,
    InspectionStatus,
    ObservedItem,
    OrderLine,
    Verdict,
)


# ---------------------------------------------------------------------------
# Test Case Definition
# ---------------------------------------------------------------------------

@dataclass
class EvalCase:
    """A single evaluation test case with ground truth."""
    case_id: str
    scenario: str
    description: str
    expected_lines: list[OrderLine]
    observed_items: list[ObservedItem]
    image_quality_ok: bool = True
    image_quality_uncertain: bool = False
    # Ground truth
    expected_decision: Decision = Decision.SEAL
    expected_checks: dict[str, Verdict] = field(default_factory=dict)
    notes: str = ""


# ---------------------------------------------------------------------------
# Fixtures — 50+ eval units covering all 8 official scenarios
# ---------------------------------------------------------------------------

def build_eval_dataset() -> list[EvalCase]:
    """Build the evaluation dataset with ground truth labels."""
    cases: list[EvalCase] = []

    def ol(sku: str, qty: int) -> OrderLine:
        return OrderLine(sku=sku, quantity=qty)

    def oi(sku: Optional[str], name: str, qty: int, conf: float) -> ObservedItem:
        return ObservedItem(sku=sku, name=name, observed_quantity=qty, confidence=conf)

    # === SCENARIO 1: CORRECT ORDER (10 cases) ===
    for i in range(1, 11):
        cases.append(EvalCase(
            case_id=f"CORRECT-{i:03d}",
            scenario="correct_order",
            description=f"Correct order #{i}",
            expected_lines=[ol(f"SKU-{i:03d}", 1 + (i % 3))],
            observed_items=[oi(f"SKU-{i:03d}", f"Product {i}", 1 + (i % 3), 0.92)],
            expected_decision=Decision.SEAL,
            expected_checks={
                CheckKey.ITEMS_PRESENT.value: Verdict.PASS_,
                CheckKey.QUANTITY_MATCH.value: Verdict.PASS_,
                CheckKey.NO_EXTRA_ITEMS.value: Verdict.PASS_,
            },
        ))

    # === SCENARIO 2: MISSING ITEM (8 cases) ===
    for i in range(1, 9):
        cases.append(EvalCase(
            case_id=f"MISSING-{i:03d}",
            scenario="missing_item",
            description=f"Missing item #{i}",
            expected_lines=[ol(f"SKU-A{i}", 1), ol(f"SKU-B{i}", 1)],
            observed_items=[oi(f"SKU-A{i}", f"Product A{i}", 1, 0.9)],
            expected_decision=Decision.STOP_AND_FIX,
            expected_checks={
                CheckKey.ITEMS_PRESENT.value: Verdict.FAIL,
            },
        ))

    # === SCENARIO 3: WRONG ITEM (6 cases) ===
    for i in range(1, 7):
        cases.append(EvalCase(
            case_id=f"WRONG-{i:03d}",
            scenario="wrong_item",
            description=f"Wrong item #{i} — expected X, got Y",
            expected_lines=[ol(f"SKU-EXPECTED-{i}", 1)],
            observed_items=[oi(f"SKU-WRONG-{i}", f"Wrong Product {i}", 1, 0.88)],
            expected_decision=Decision.STOP_AND_FIX,
            expected_checks={
                CheckKey.ITEMS_PRESENT.value: Verdict.FAIL,
                CheckKey.NO_EXTRA_ITEMS.value: Verdict.FAIL,
            },
        ))

    # === SCENARIO 4: EXTRA ITEM (6 cases) ===
    for i in range(1, 7):
        cases.append(EvalCase(
            case_id=f"EXTRA-{i:03d}",
            scenario="extra_item",
            description=f"Extra item #{i}",
            expected_lines=[ol(f"SKU-{i:03d}", 1)],
            observed_items=[
                oi(f"SKU-{i:03d}", f"Correct Item {i}", 1, 0.91),
                oi(f"SKU-EXTRA-{i}", f"Extra Item {i}", 1, 0.85),
            ],
            expected_decision=Decision.STOP_AND_FIX,
            expected_checks={
                CheckKey.NO_EXTRA_ITEMS.value: Verdict.FAIL,
            },
        ))

    # === SCENARIO 5: WRONG QUANTITY (6 cases) ===
    for i in range(1, 4):
        # Too few
        cases.append(EvalCase(
            case_id=f"QTY-FEW-{i:03d}",
            scenario="wrong_quantity",
            description=f"Quantity too few #{i}",
            expected_lines=[ol(f"SKU-{i:03d}", 3)],
            observed_items=[oi(f"SKU-{i:03d}", f"Product {i}", 2, 0.9)],
            expected_decision=Decision.STOP_AND_FIX,
            expected_checks={
                CheckKey.QUANTITY_MATCH.value: Verdict.FAIL,
            },
        ))
        # Too many
        cases.append(EvalCase(
            case_id=f"QTY-MANY-{i:03d}",
            scenario="wrong_quantity",
            description=f"Quantity too many #{i}",
            expected_lines=[ol(f"SKU-{i:03d}", 1)],
            observed_items=[oi(f"SKU-{i:03d}", f"Product {i}", 3, 0.9)],
            expected_decision=Decision.STOP_AND_FIX,
            expected_checks={
                CheckKey.QUANTITY_MATCH.value: Verdict.FAIL,
            },
        ))

    # === SCENARIO 6: MULTIPLE IDENTICAL (5 cases) ===
    for i in range(1, 6):
        qty = 2 + i
        cases.append(EvalCase(
            case_id=f"MULTI-{i:03d}",
            scenario="multiple_identical",
            description=f"Multiple identical ({qty}x) #{i}",
            expected_lines=[ol(f"SKU-MULTI-{i}", qty)],
            observed_items=[oi(f"SKU-MULTI-{i}", f"Product Multi {i}", qty, 0.88)],
            expected_decision=Decision.SEAL,
        ))

    # === SCENARIO 7: VISUALLY SIMILAR (low confidence) (5 cases) ===
    for i in range(1, 6):
        cases.append(EvalCase(
            case_id=f"SIMILAR-{i:03d}",
            scenario="visually_similar",
            description=f"Visually similar product #{i} — low confidence ID",
            expected_lines=[ol(f"SKU-SIMILAR-{i}", 1)],
            observed_items=[oi(f"SKU-SIMILAR-{i}", f"Maybe Similar {i}", 1, 0.35)],
            expected_decision=Decision.STOP_AND_FIX,
            notes="Expected UNCERTAIN/PENDING_REVIEW due to low confidence",
        ))

    # === SCENARIO 8: AMBIGUOUS PHOTOS (5 cases) ===
    for i in range(1, 4):
        cases.append(EvalCase(
            case_id=f"AMBIG-QUAL-{i:03d}",
            scenario="ambiguous_photos",
            description=f"Ambiguous photo quality #{i}",
            expected_lines=[ol(f"SKU-{i:03d}", 1)],
            observed_items=[oi(f"SKU-{i:03d}", f"Product {i}", 1, 0.9)],
            image_quality_ok=False,
            expected_decision=Decision.STOP_AND_FIX,
        ))
    for i in range(1, 3):
        cases.append(EvalCase(
            case_id=f"AMBIG-UNC-{i:03d}",
            scenario="ambiguous_photos",
            description=f"Uncertain image quality #{i}",
            expected_lines=[ol(f"SKU-{i:03d}", 1)],
            observed_items=[oi(f"SKU-{i:03d}", f"Product {i}", 1, 0.9)],
            image_quality_uncertain=True,
            expected_decision=Decision.STOP_AND_FIX,
            notes="Expected PENDING_REVIEW status",
        ))

    return cases


# ---------------------------------------------------------------------------
# Evaluation Runner
# ---------------------------------------------------------------------------

@dataclass
class EvalMetrics:
    """Aggregated evaluation metrics."""
    total: int = 0
    correct_decisions: int = 0
    false_pass: int = 0   # Dangerous: should be STOP but said SEAL
    false_stop: int = 0   # Annoying: should be SEAL but said STOP
    per_scenario: dict = field(default_factory=lambda: defaultdict(lambda: {"total": 0, "correct": 0}))
    per_check: dict = field(default_factory=lambda: defaultdict(lambda: {"tp": 0, "fp": 0, "fn": 0, "tn": 0, "uncertain": 0}))
    uncertain_count: int = 0
    pending_review_count: int = 0
    failures: list = field(default_factory=list)


def run_evaluation(cases: list[EvalCase]) -> EvalMetrics:
    """Run all eval cases through the decision engine and compute metrics."""
    metrics = EvalMetrics()

    for case in cases:
        metrics.total += 1
        metrics.per_scenario[case.scenario]["total"] += 1

        result = run_decision_engine(
            expected_lines=case.expected_lines,
            observed_items=case.observed_items,
            image_quality_ok=case.image_quality_ok,
            image_quality_uncertain=case.image_quality_uncertain,
            model_version="eval-harness",
        )

        decision_correct = result.decision == case.expected_decision

        if decision_correct:
            metrics.correct_decisions += 1
            metrics.per_scenario[case.scenario]["correct"] += 1
        else:
            metrics.failures.append({
                "case_id": case.case_id,
                "scenario": case.scenario,
                "expected": case.expected_decision.value,
                "actual": result.decision.value,
                "description": case.description,
            })

        # False-PASS is operationally dangerous
        if result.decision == Decision.SEAL and case.expected_decision == Decision.STOP_AND_FIX:
            metrics.false_pass += 1

        # False-STOP is annoying but safe
        if result.decision == Decision.STOP_AND_FIX and case.expected_decision == Decision.SEAL:
            metrics.false_stop += 1

        # Track UNCERTAIN / PENDING_REVIEW
        if result.status == InspectionStatus.PENDING_REVIEW:
            metrics.pending_review_count += 1
        if any(c.verdict == Verdict.UNCERTAIN for c in result.checks):
            metrics.uncertain_count += 1

        # Per-check metrics
        for check in result.checks:
            key = check.check_key.value
            expected_verdict = case.expected_checks.get(key)
            if expected_verdict is not None:
                if check.verdict == Verdict.UNCERTAIN:
                    metrics.per_check[key]["uncertain"] += 1
                elif check.verdict == expected_verdict:
                    if expected_verdict in (Verdict.PASS_,):
                        metrics.per_check[key]["tn"] += 1
                    else:
                        metrics.per_check[key]["tp"] += 1
                else:
                    if expected_verdict == Verdict.FAIL and check.verdict == Verdict.PASS_:
                        metrics.per_check[key]["fn"] += 1
                    elif expected_verdict == Verdict.PASS_ and check.verdict == Verdict.FAIL:
                        metrics.per_check[key]["fp"] += 1

    return metrics


def format_report(metrics: EvalMetrics) -> str:
    """Format evaluation results as a Markdown report."""
    lines = []
    lines.append("# Pack Manager — Evaluation Report")
    lines.append("")
    lines.append("## Summary")
    lines.append("")
    accuracy = metrics.correct_decisions / metrics.total * 100 if metrics.total else 0
    lines.append(f"| Metric | Value |")
    lines.append(f"|---|---|")
    lines.append(f"| Total cases | {metrics.total} |")
    lines.append(f"| Correct decisions | {metrics.correct_decisions} |")
    lines.append(f"| **Decision accuracy** | **{accuracy:.1f}%** |")
    lines.append(f"| **False PASS (dangerous)** | **{metrics.false_pass}** |")
    lines.append(f"| False STOP (safe) | {metrics.false_stop} |")
    lines.append(f"| UNCERTAIN verdicts | {metrics.uncertain_count} |")
    lines.append(f"| Pending review | {metrics.pending_review_count} |")
    lines.append("")

    lines.append("## Per-Scenario Accuracy")
    lines.append("")
    lines.append("| Scenario | Total | Correct | Accuracy |")
    lines.append("|---|---|---|---|")
    for scenario, data in sorted(metrics.per_scenario.items()):
        acc = data["correct"] / data["total"] * 100 if data["total"] else 0
        lines.append(f"| {scenario} | {data['total']} | {data['correct']} | {acc:.0f}% |")
    lines.append("")

    if metrics.per_check:
        lines.append("## Per-Check Metrics")
        lines.append("")
        lines.append("| Check | TP | FP | FN | TN | UNCERTAIN |")
        lines.append("|---|---|---|---|---|---|")
        for check, data in sorted(metrics.per_check.items()):
            lines.append(f"| {check} | {data['tp']} | {data['fp']} | {data['fn']} | {data['tn']} | {data['uncertain']} |")
        lines.append("")

    if metrics.failures:
        lines.append("## Failures")
        lines.append("")
        lines.append("| Case | Scenario | Expected | Actual | Description |")
        lines.append("|---|---|---|---|---|")
        for f in metrics.failures:
            lines.append(f"| {f['case_id']} | {f['scenario']} | {f['expected']} | {f['actual']} | {f['description']} |")
        lines.append("")

    lines.append("## Methodology")
    lines.append("")
    lines.append("- Decision engine evaluated deterministically (no VLM in this phase)")
    lines.append("- Ground truth labels defined per official PCK test scenarios")
    lines.append("- False PASS is the headline safety metric (approving incorrect packages)")
    lines.append("- UNCERTAIN correctly routes to human review, never auto-seals")
    lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI Entry Point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Run Pack Manager evaluation")
    parser.add_argument("--output", default="docs/EVALUATION.md", help="Output file path")
    args = parser.parse_args()

    cases = build_eval_dataset()
    print(f"Running evaluation with {len(cases)} cases...")

    metrics = run_evaluation(cases)
    report = format_report(metrics)

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(report, encoding="utf-8")

    print(report)
    print(f"\nReport written to {output_path}")


if __name__ == "__main__":
    main()
