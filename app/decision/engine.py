"""
PCK Pack Manager — Deterministic Decision Engine

This module makes the final operational decision (SEAL / STOP_AND_FIX / UNCERTAIN)
using only deterministic logic. The VLM provides observations; this engine provides control.

Rules (from the official challenge):
  - SEAL:         every expected item observed at correct qty, no extras, no wrong items,
                  sufficient image quality, zero UNCERTAIN checks.
  - STOP_AND_FIX: any reliable mismatch (missing / wrong / extra / quantity).
  - UNCERTAIN:    no FAIL, but at least one check is UNCERTAIN → human review.
  - PENDING:      model/API failure → record saved, operator unblocked (fail-open).

A single failed check must never be hidden by an overall PASS.
Uncertainty must never be silently treated as PASS.
"""

from __future__ import annotations

from app.domain.schemas import (
    Check,
    CheckKey,
    Decision,
    InspectionStatus,
    ItemDiscrepancy,
    ObservedItem,
    OrderLine,
    Outcome,
    UncertaintyDetail,
    UncertaintyReason,
    Verdict,
)


class DecisionResult:
    """Container for the decision engine output."""

    def __init__(
        self,
        decision: Decision,
        checks: list[Check],
        discrepancies: list[ItemDiscrepancy],
        uncertainties: list[UncertaintyDetail],
        status: InspectionStatus = InspectionStatus.COMPLETED,
    ):
        self.decision = decision
        self.checks = checks
        self.discrepancies = discrepancies
        self.uncertainties = uncertainties
        self.status = status

    @property
    def outcome(self) -> Outcome:
        return Outcome(decision=self.decision)


def run_decision_engine(
    expected_lines: list[OrderLine],
    observed_items: list[ObservedItem],
    image_quality_ok: bool = True,
    image_quality_uncertain: bool = False,
    model_version: str = "",
    latency_ms: float = 0.0,
) -> DecisionResult:
    """
    Compare expected order lines against VLM-observed items and produce
    a deterministic decision with per-check verdicts.

    Args:
        expected_lines: What should be in the box (from the order).
        observed_items: What the VLM detected in the box.
        image_quality_ok: Whether image quality is sufficient.
        image_quality_uncertain: Whether image quality is ambiguous.
        model_version: Model identifier for evidence records.
        latency_ms: VLM call latency for evidence records.

    Returns:
        DecisionResult with decision, checks, discrepancies, uncertainties.
    """
    checks: list[Check] = []
    discrepancies: list[ItemDiscrepancy] = []
    uncertainties: list[UncertaintyDetail] = []

    # Build lookup maps
    expected_map: dict[str, int] = {line.sku: line.quantity for line in expected_lines}
    observed_map: dict[str, ObservedItem] = {}
    for item in observed_items:
        if item.sku:
            if item.sku in observed_map:
                existing = observed_map[item.sku]
                observed_map[item.sku] = ObservedItem(
                    sku=item.sku,
                    name=item.name,
                    observed_quantity=existing.observed_quantity + item.observed_quantity,
                    confidence=min(existing.confidence, item.confidence),
                    evidence_image_ids=existing.evidence_image_ids + item.evidence_image_ids,
                    observation_text=f"{existing.observation_text}; {item.observation_text}",
                )
            else:
                observed_map[item.sku] = item

    observed_qty_map: dict[str, int] = {
        sku: item.observed_quantity for sku, item in observed_map.items()
    }

    # Collect unknown items (no SKU match)
    unknown_items = [item for item in observed_items if item.sku is None]

    # -----------------------------------------------------------------------
    # CHECK 1: IMAGE QUALITY
    # -----------------------------------------------------------------------
    if image_quality_uncertain:
        iq_check = Check(
            check_key=CheckKey.IMAGE_QUALITY,
            verdict=Verdict.UNCERTAIN,
            confidence=0.5,
            detail="Image quality is ambiguous — may affect identification reliability",
            model_version=model_version,
            latency_ms=0.0,
        )
        uncertainties.append(UncertaintyDetail(
            reason_code=UncertaintyReason.BLUR,
            what_is_known="Images were received and processed",
            what_is_unknown="Whether image quality is sufficient for reliable product identification",
            missing_evidence="Clear, well-lit photographs of the open package",
            recommended_action="Retake photographs with better lighting and focus",
        ))
    elif not image_quality_ok:
        iq_check = Check(
            check_key=CheckKey.IMAGE_QUALITY,
            verdict=Verdict.FAIL,
            confidence=0.9,
            detail="Image quality insufficient for reliable verification",
            model_version=model_version,
            latency_ms=0.0,
        )
    else:
        iq_check = Check(
            check_key=CheckKey.IMAGE_QUALITY,
            verdict=Verdict.PASS_,
            confidence=1.0,
            detail="Image quality sufficient for verification",
            model_version=model_version,
            latency_ms=0.0,
        )
    checks.append(iq_check)

    # -----------------------------------------------------------------------
    # CHECK 2: ITEMS PRESENT (are all expected items found?)
    # -----------------------------------------------------------------------
    missing_skus = [sku for sku in expected_map if sku not in observed_qty_map]
    uncertain_skus = [
        sku for sku in expected_map
        if sku in observed_map and observed_map[sku].confidence < 0.5
    ]

    if missing_skus:
        items_check = Check(
            check_key=CheckKey.ITEMS_PRESENT,
            verdict=Verdict.FAIL,
            confidence=0.9,
            detail=f"Missing items: {', '.join(missing_skus)}",
            model_version=model_version,
            latency_ms=latency_ms,
        )
        for sku in missing_skus:
            discrepancies.append(ItemDiscrepancy(
                sku=sku,
                product_name=sku,
                expected_quantity=expected_map[sku],
                observed_quantity=0,
                discrepancy_type="missing",
                detail=f"SKU {sku} expected but not found in package",
            ))
    elif uncertain_skus:
        items_check = Check(
            check_key=CheckKey.ITEMS_PRESENT,
            verdict=Verdict.UNCERTAIN,
            confidence=0.4,
            detail=f"Low confidence identification for: {', '.join(uncertain_skus)}",
            model_version=model_version,
            latency_ms=latency_ms,
        )
        for sku in uncertain_skus:
            uncertainties.append(UncertaintyDetail(
                reason_code=UncertaintyReason.MODEL_UNCERTAINTY,
                what_is_known=f"An item possibly matching {sku} was detected",
                what_is_unknown=f"Whether the detected item is actually {sku}",
                missing_evidence="Clearer image or additional viewing angle",
                recommended_action=f"Manually verify item identity for {sku}",
            ))
    else:
        items_check = Check(
            check_key=CheckKey.ITEMS_PRESENT,
            verdict=Verdict.PASS_,
            confidence=0.95,
            detail="All expected items identified in the package",
            model_version=model_version,
            latency_ms=latency_ms,
        )
    checks.append(items_check)

    # -----------------------------------------------------------------------
    # CHECK 3: QUANTITY MATCH
    # -----------------------------------------------------------------------
    qty_mismatches: list[str] = []
    qty_uncertain: list[str] = []

    for sku, expected_qty in expected_map.items():
        observed_qty = observed_qty_map.get(sku, 0)
        if sku in missing_skus:
            continue  # already handled in items_present
        if observed_qty != expected_qty:
            qty_mismatches.append(sku)
            discrepancies.append(ItemDiscrepancy(
                sku=sku,
                product_name=observed_map[sku].name if sku in observed_map else sku,
                expected_quantity=expected_qty,
                observed_quantity=observed_qty,
                discrepancy_type="wrong_quantity",
                detail=f"Expected {expected_qty}, observed {observed_qty}",
            ))
        elif sku in observed_map and observed_map[sku].confidence < 0.5:
            qty_uncertain.append(sku)

    if qty_mismatches:
        qty_check = Check(
            check_key=CheckKey.QUANTITY_MATCH,
            verdict=Verdict.FAIL,
            confidence=0.9,
            detail=f"Quantity mismatch for: {', '.join(qty_mismatches)}",
            model_version=model_version,
            latency_ms=latency_ms,
        )
    elif qty_uncertain:
        qty_check = Check(
            check_key=CheckKey.QUANTITY_MATCH,
            verdict=Verdict.UNCERTAIN,
            confidence=0.4,
            detail=f"Quantity uncertain for: {', '.join(qty_uncertain)}",
            model_version=model_version,
            latency_ms=latency_ms,
        )
    else:
        qty_check = Check(
            check_key=CheckKey.QUANTITY_MATCH,
            verdict=Verdict.PASS_,
            confidence=0.95,
            detail="All quantities match expected order",
            model_version=model_version,
            latency_ms=latency_ms,
        )
    checks.append(qty_check)

    # -----------------------------------------------------------------------
    # CHECK 4: NO EXTRA ITEMS
    # -----------------------------------------------------------------------
    extra_skus = [sku for sku in observed_qty_map if sku not in expected_map]

    if extra_skus or unknown_items:
        extra_detail_parts = []
        if extra_skus:
            extra_detail_parts.append(f"Unexpected SKUs: {', '.join(extra_skus)}")
            for sku in extra_skus:
                discrepancies.append(ItemDiscrepancy(
                    sku=sku,
                    product_name=observed_map[sku].name,
                    expected_quantity=0,
                    observed_quantity=observed_map[sku].observed_quantity,
                    discrepancy_type="extra",
                    detail=f"SKU {sku} found but not in order",
                ))
        if unknown_items:
            names = [item.name for item in unknown_items]
            extra_detail_parts.append(f"Unidentified items: {', '.join(names)}")
            for item in unknown_items:
                discrepancies.append(ItemDiscrepancy(
                    sku=None,
                    product_name=item.name,
                    expected_quantity=0,
                    observed_quantity=item.observed_quantity,
                    discrepancy_type="unknown_item",
                    detail=f"Unidentified item '{item.name}' found in package",
                ))

        # If all extras are low-confidence unknowns, mark uncertain instead of fail
        all_low_conf = (
            not extra_skus
            and all(item.confidence < 0.5 for item in unknown_items)
        )

        extra_check = Check(
            check_key=CheckKey.NO_EXTRA_ITEMS,
            verdict=Verdict.UNCERTAIN if all_low_conf else Verdict.FAIL,
            confidence=0.4 if all_low_conf else 0.9,
            detail="; ".join(extra_detail_parts),
            model_version=model_version,
            latency_ms=latency_ms,
        )
        if all_low_conf:
            for item in unknown_items:
                uncertainties.append(UncertaintyDetail(
                    reason_code=UncertaintyReason.MODEL_UNCERTAINTY,
                    what_is_known=f"Something resembling '{item.name}' detected",
                    what_is_unknown="Whether this is an actual extra item or a misidentification",
                    missing_evidence="Clearer image or different angle",
                    recommended_action="Manually verify whether extra items are present",
                ))
    else:
        extra_check = Check(
            check_key=CheckKey.NO_EXTRA_ITEMS,
            verdict=Verdict.PASS_,
            confidence=0.95,
            detail="No unexpected items detected",
            model_version=model_version,
            latency_ms=latency_ms,
        )
    checks.append(extra_check)

    # -----------------------------------------------------------------------
    # CHECK 5: NO WRONG ITEMS
    # A wrong item is a substitution: at least one expected SKU is absent and
    # at least one unexpected SKU (or unidentified item) is present. Pure
    # extras (expected all present + unexpected also present) stay on
    # no_extra_items only. Pure missing stays on items_present only.
    # -----------------------------------------------------------------------
    substitution = bool(missing_skus) and (bool(extra_skus) or bool(unknown_items))
    if substitution:
        # Re-label the paired missing/extra discrepancies as wrong_item for
        # the evidence trail (one substitution finding, not two unrelated ones).
        for d in discrepancies:
            if d.discrepancy_type in ("missing", "extra", "unknown_item"):
                d.discrepancy_type = "wrong_item"
                d.detail = f"Substitution: {d.detail}"
        names = [sku for sku in missing_skus] + list(extra_skus)
        names += [item.name for item in unknown_items]
        wrong_check = Check(
            check_key=CheckKey.NO_WRONG_ITEMS,
            verdict=Verdict.FAIL,
            confidence=0.9,
            detail=f"Wrong / substituted items involving: {', '.join(names)}",
            model_version=model_version,
            latency_ms=latency_ms,
        )
    else:
        wrong_check = Check(
            check_key=CheckKey.NO_WRONG_ITEMS,
            verdict=Verdict.PASS_,
            confidence=0.95,
            detail="No wrong-item substitution detected",
            model_version=model_version,
            latency_ms=latency_ms,
        )
    checks.append(wrong_check)

    # -----------------------------------------------------------------------
    # FINAL DECISION
    # -----------------------------------------------------------------------
    has_fail = any(c.verdict == Verdict.FAIL for c in checks)
    has_uncertain = any(c.verdict == Verdict.UNCERTAIN for c in checks)

    if has_fail:
        decision = Decision.STOP_AND_FIX
        status = InspectionStatus.COMPLETED
    elif has_uncertain:
        # UNCERTAIN never auto-seals — route to human review
        decision = Decision.STOP_AND_FIX
        status = InspectionStatus.PENDING_REVIEW
    else:
        decision = Decision.SEAL
        status = InspectionStatus.COMPLETED

    return DecisionResult(
        decision=decision,
        checks=checks,
        discrepancies=discrepancies,
        uncertainties=uncertainties,
        status=status,
    )
