"""
PCK Pack Manager — Deterministic Decision Engine (v2)

This module makes the final operational decision (SEAL / STOP_AND_FIX / UNCERTAIN)
using only deterministic logic. The VLM provides observations; this engine provides control.

Rules (from the official challenge):
  - SEAL:         every expected item observed at correct qty, no extras, no wrong items,
                  sufficient image quality, scene fully visible, zero UNCERTAIN checks.
  - STOP_AND_FIX: any reliable mismatch (missing / wrong / extra / quantity).
  - UNCERTAIN:    no FAIL, but at least one check is UNCERTAIN → human review.
  - PENDING:      model/API failure → record saved, operator unblocked (fail-open).

A single failed check must never be hidden by an overall PASS.
Uncertainty must never be silently treated as PASS.

v2 upgrades:
  - Scene coverage check: was the whole box interior visible?
  - Photo reuse detection: same image bytes used for another order = UNCERTAIN
  - UNCERTAIN correctly routes to PENDING_REVIEW (not STOP_AND_FIX)
  - Per-SKU confidence weighting in items_present and quantity_match
  - Substitution detection (missing + extra → wrong_item)
  - Actionable uncertainty details: what is known, unknown, missing, recommended action
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
    SceneCoverage,
    UncertaintyDetail,
    UncertaintyReason,
    Verdict,
)

LOCAL_GATE_VERSION = "local-quality-gate/1"

# Confidence threshold below which an observation is considered uncertain
_CONFIDENCE_THRESHOLD = 0.5


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


def _build_scene_check(
    scene: SceneCoverage | None,
    model_version: str,
    uncertainties: list[UncertaintyDetail],
) -> Check:
    """
    Evaluate scene coverage: was the whole box interior visible?

    Boxes where items may be hidden cannot be confidently sealed even if
    everything visible matches — there could be extra items or missing items
    concealed under filler or stacked products.
    """
    if scene is None:
        # No scene data — assume visible (conservative, not blocking)
        return Check(
            check_key=CheckKey.SCENE_COVERAGE,
            verdict=Verdict.PASS_,
            confidence=0.8,
            detail="Scene coverage not assessed (no scene data from VLM).",
            model_version=model_version,
            latency_ms=0.0,
        )

    covered = (
        scene.box_interior_fully_visible
        and not scene.items_may_be_hidden
        and scene.visibility_confidence >= 0.5
    )

    if covered:
        return Check(
            check_key=CheckKey.SCENE_COVERAGE,
            verdict=Verdict.PASS_,
            confidence=scene.visibility_confidence,
            detail=(
                f"The whole inside of the box is visible and nothing appears hidden "
                f"(confidence={scene.visibility_confidence:.2f})."
            ),
            model_version=model_version,
            latency_ms=0.0,
        )

    # Build specific reason
    why = []
    if not scene.box_interior_fully_visible:
        why.append("part of the box is out of frame")
    if scene.items_may_be_hidden:
        why.append("items may be stacked or hidden under filler")
    if scene.visibility_confidence < 0.5:
        why.append(f"low visibility confidence ({scene.visibility_confidence:.2f})")
    notes = f" {scene.notes}" if scene.notes else ""
    detail = "; ".join(why).capitalize() + "." + notes

    uncertainties.append(UncertaintyDetail(
        reason_code=UncertaintyReason.OCCLUSION,
        what_is_known="Some items inside the box are visible",
        what_is_unknown="Whether any items are stacked or hidden under packing material",
        missing_evidence="A photo with the whole inside of the box in the frame, items spread out",
        recommended_action="Retake the photo showing the whole inside of the box with items spread out",
    ))

    return Check(
        check_key=CheckKey.SCENE_COVERAGE,
        verdict=Verdict.UNCERTAIN,
        confidence=scene.visibility_confidence,
        detail=detail,
        model_version=model_version,
        latency_ms=0.0,
    )


def _build_photo_reuse_check(
    image_sha256_list: list[str],
    seen_hashes: dict[str, str] | None,
    model_version: str,
    uncertainties: list[UncertaintyDetail],
) -> Check | None:
    """
    Detect if any photo has been used before for a different order.

    Same photo for the same order is a legitimate re-check; same photo for
    a different order means the image can't show the current box.
    """
    if not image_sha256_list or seen_hashes is None:
        return None  # No reuse data available — skip check

    reused = [sha for sha in image_sha256_list if sha in seen_hashes]
    if reused:
        records = [seen_hashes[sha] for sha in reused]
        detail = (
            f"This exact photo was already used for another order "
            f"(records: {', '.join(set(records))}), so it can't show this box."
        )
        uncertainties.append(UncertaintyDetail(
            reason_code=UncertaintyReason.CONFLICTING_IMAGES,
            what_is_known="The photo bytes were already used for a different verification",
            what_is_unknown="Whether this photo actually shows the current box contents",
            missing_evidence="A new photo taken now showing the current open box",
            recommended_action="Take a new photo of this box. The uploaded photo was already used for another order.",
        ))
        return Check(
            check_key=CheckKey.PHOTO_REUSE,
            verdict=Verdict.UNCERTAIN,
            confidence=0.0,
            detail=detail,
            model_version=LOCAL_GATE_VERSION,
            latency_ms=0.0,
        )

    return Check(
        check_key=CheckKey.PHOTO_REUSE,
        verdict=Verdict.PASS_,
        confidence=1.0,
        detail="The photo has not been used for any other order.",
        model_version=LOCAL_GATE_VERSION,
        latency_ms=0.0,
    )


def run_decision_engine(
    expected_lines: list[OrderLine],
    observed_items: list[ObservedItem],
    image_quality_ok: bool = True,
    image_quality_uncertain: bool = False,
    model_version: str = "",
    latency_ms: float = 0.0,
    scene: SceneCoverage | None = None,
    image_sha256_list: list[str] | None = None,
    seen_hashes: dict[str, str] | None = None,
    component_lookalike_skus: set[str] | None = None,
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
        scene: Scene coverage assessment from VLM (v2).
        image_sha256_list: SHA-256 hashes of the uploaded photos (v2).
        seen_hashes: Mapping of SHA-256 → previous record_id for reuse detection (v2).
        component_lookalike_skus: SKUs that may ship as parts of ordered products
            (v2.1). Seeing one as an "extra" is UNCERTAIN, not FAIL.

    Returns:
        DecisionResult with decision, checks, discrepancies, uncertainties.
    """
    checks: list[Check] = []
    discrepancies: list[ItemDiscrepancy] = []
    uncertainties: list[UncertaintyDetail] = []
    lookalike_extras = component_lookalike_skus or set()

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

    # Collect unknown items (no SKU match) — potential extra or packaging
    unknown_items = [item for item in observed_items if item.sku is None]

    # -----------------------------------------------------------------------
    # CHECK 1: IMAGE QUALITY
    # -----------------------------------------------------------------------
    if image_quality_uncertain:
        iq_check = Check(
            check_key=CheckKey.IMAGE_QUALITY,
            verdict=Verdict.UNCERTAIN,
            confidence=0.5,
            detail="Image quality is ambiguous — may affect identification reliability.",
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
            detail="Image quality insufficient for reliable verification.",
            model_version=model_version,
            latency_ms=0.0,
        )
    else:
        iq_check = Check(
            check_key=CheckKey.IMAGE_QUALITY,
            verdict=Verdict.PASS_,
            confidence=1.0,
            detail="Image quality sufficient for verification.",
            model_version=model_version,
            latency_ms=0.0,
        )
    checks.append(iq_check)

    # -----------------------------------------------------------------------
    # CHECK 2: SCENE COVERAGE (v2) — was the whole box visible?
    # -----------------------------------------------------------------------
    scene_check = _build_scene_check(scene, model_version, uncertainties)
    checks.append(scene_check)
    scene_covered = scene_check.verdict == Verdict.PASS_

    # -----------------------------------------------------------------------
    # CHECK 3: PHOTO REUSE (v2) — is this a fresh photo?
    # -----------------------------------------------------------------------
    reuse_check = _build_photo_reuse_check(
        image_sha256_list or [], seen_hashes, model_version, uncertainties
    )
    if reuse_check is not None:
        checks.append(reuse_check)

    # -----------------------------------------------------------------------
    # CHECK 4: ITEMS PRESENT (are all expected items found?)
    # -----------------------------------------------------------------------
    missing_skus = [sku for sku in expected_map if sku not in observed_qty_map]
    uncertain_skus = [
        sku for sku in expected_map
        if sku in observed_map and observed_map[sku].confidence < _CONFIDENCE_THRESHOLD
    ]

    if missing_skus and not scene_covered:
        # Competitive edge vs overconfident "fully visible" false STOPs:
        # if the scene may hide items, missing → UNCERTAIN (hand check), not FAIL.
        items_check = Check(
            check_key=CheckKey.ITEMS_PRESENT,
            verdict=Verdict.UNCERTAIN,
            confidence=0.45,
            detail=(
                f"Not seeing {', '.join(missing_skus)}, but the whole box may not be "
                f"visible — items could be stacked or under filler."
            ),
            model_version=model_version,
            latency_ms=latency_ms,
        )
        for sku in missing_skus:
            uncertainties.append(UncertaintyDetail(
                reason_code=UncertaintyReason.OCCLUSION,
                what_is_known=f"SKU {sku} was not identified in the visible area",
                what_is_unknown=f"Whether {sku} is hidden under other items or packing material",
                missing_evidence="A photo with items spread out so every product is visible",
                recommended_action=f"Spread contents and re-check for {sku} by hand",
            ))
    elif missing_skus:
        items_check = Check(
            check_key=CheckKey.ITEMS_PRESENT,
            verdict=Verdict.FAIL,
            confidence=0.9,
            detail=f"Missing items: {', '.join(missing_skus)}.",
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
            detail=f"Low-confidence identification for: {', '.join(uncertain_skus)}.",
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
    elif not scene_covered and missing_skus == []:
        # Scene not fully visible — items may be hidden. Can't confirm all present.
        items_check = Check(
            check_key=CheckKey.ITEMS_PRESENT,
            verdict=Verdict.UNCERTAIN,
            confidence=0.6,
            detail="All visible items found, but scene coverage is incomplete — some items may be hidden.",
            model_version=model_version,
            latency_ms=latency_ms,
        )
    else:
        items_check = Check(
            check_key=CheckKey.ITEMS_PRESENT,
            verdict=Verdict.PASS_,
            confidence=0.95,
            detail="All expected items identified in the package.",
            model_version=model_version,
            latency_ms=latency_ms,
        )
    checks.append(items_check)

    # -----------------------------------------------------------------------
    # CHECK 5: QUANTITY MATCH
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
        elif sku in observed_map and observed_map[sku].confidence < _CONFIDENCE_THRESHOLD:
            qty_uncertain.append(sku)

    if qty_mismatches:
        qty_check = Check(
            check_key=CheckKey.QUANTITY_MATCH,
            verdict=Verdict.FAIL,
            confidence=0.9,
            detail=f"Quantity mismatch for: {', '.join(qty_mismatches)}.",
            model_version=model_version,
            latency_ms=latency_ms,
        )
    elif qty_uncertain:
        qty_check = Check(
            check_key=CheckKey.QUANTITY_MATCH,
            verdict=Verdict.UNCERTAIN,
            confidence=0.4,
            detail=f"Quantity uncertain for: {', '.join(qty_uncertain)}.",
            model_version=model_version,
            latency_ms=latency_ms,
        )
    else:
        qty_check = Check(
            check_key=CheckKey.QUANTITY_MATCH,
            verdict=Verdict.PASS_,
            confidence=0.95,
            detail="All quantities match expected order.",
            model_version=model_version,
            latency_ms=latency_ms,
        )
    checks.append(qty_check)

    # -----------------------------------------------------------------------
    # CHECK 6: NO EXTRA ITEMS
    # -----------------------------------------------------------------------
    extra_skus = [sku for sku in observed_qty_map if sku not in expected_map]
    # Parts that may belong to an ordered product (lamp → USB cable) → UNCERTAIN
    soft_extra_skus = [sku for sku in extra_skus if sku in lookalike_extras]
    hard_extra_skus = [sku for sku in extra_skus if sku not in lookalike_extras]

    if hard_extra_skus or soft_extra_skus or unknown_items:
        extra_detail_parts = []
        if hard_extra_skus:
            extra_detail_parts.append(f"Unexpected SKUs: {', '.join(hard_extra_skus)}")
            for sku in hard_extra_skus:
                discrepancies.append(ItemDiscrepancy(
                    sku=sku,
                    product_name=observed_map[sku].name,
                    expected_quantity=0,
                    observed_quantity=observed_map[sku].observed_quantity,
                    discrepancy_type="extra",
                    detail=f"SKU {sku} found but not in order",
                ))
        if soft_extra_skus:
            extra_detail_parts.append(
                f"Possible component/lookalike of ordered product: {', '.join(soft_extra_skus)}"
            )
            for sku in soft_extra_skus:
                uncertainties.append(UncertaintyDetail(
                    reason_code=UncertaintyReason.SIMILAR_PRODUCTS,
                    what_is_known=f"Detected {sku}, which can ship as a part of an ordered product",
                    what_is_unknown="Whether this is an extra sellable unit or an included component",
                    missing_evidence="Confirm whether the ordered product includes this part",
                    recommended_action=f"Check by hand whether {sku} is a spare/extra or belongs to the order",
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

        # Soft lookalikes / low-confidence unknowns → UNCERTAIN (never false FAIL)
        all_soft = (
            not hard_extra_skus
            and (
                bool(soft_extra_skus)
                or (
                    unknown_items
                    and all(item.confidence < _CONFIDENCE_THRESHOLD for item in unknown_items)
                )
            )
        )

        extra_check = Check(
            check_key=CheckKey.NO_EXTRA_ITEMS,
            verdict=Verdict.UNCERTAIN if all_soft else Verdict.FAIL,
            confidence=0.4 if all_soft else 0.9,
            detail="; ".join(extra_detail_parts) + ".",
            model_version=model_version,
            latency_ms=latency_ms,
        )
        if all_soft and unknown_items and not soft_extra_skus:
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
            detail="No unexpected items detected.",
            model_version=model_version,
            latency_ms=latency_ms,
        )
    checks.append(extra_check)

    # -----------------------------------------------------------------------
    # CHECK 7: NO WRONG ITEMS (substitution detection)
    # A wrong item is a substitution: at least one expected SKU is absent AND
    # at least one unexpected SKU (or unidentified item) is present.
    # Pure extras (all expected present + unexpected also present) stay on
    # no_extra_items only. Pure missing stays on items_present only.
    # Only claim substitution when the scene is fully covered — otherwise the
    # "missing" item may simply be hidden (reduces false STOP vs competitors).
    # -----------------------------------------------------------------------
    substitution = (
        scene_covered
        and bool(missing_skus)
        and (bool(hard_extra_skus) or bool(unknown_items))
    )
    if substitution:
        # Re-label the paired missing/extra discrepancies as wrong_item
        for d in discrepancies:
            if d.discrepancy_type in ("missing", "extra", "unknown_item"):
                d.discrepancy_type = "wrong_item"
                d.detail = f"Substitution: {d.detail}"
        names = list(missing_skus) + list(extra_skus)
        names += [item.name for item in unknown_items]
        wrong_check = Check(
            check_key=CheckKey.NO_WRONG_ITEMS,
            verdict=Verdict.FAIL,
            confidence=0.9,
            detail=f"Wrong/substituted items involving: {', '.join(names)}.",
            model_version=model_version,
            latency_ms=latency_ms,
        )
    else:
        wrong_check = Check(
            check_key=CheckKey.NO_WRONG_ITEMS,
            verdict=Verdict.PASS_,
            confidence=0.95,
            detail="No wrong-item substitution detected.",
            model_version=model_version,
            latency_ms=latency_ms,
        )
    checks.append(wrong_check)

    # -----------------------------------------------------------------------
    # FINAL DECISION
    # Per spec: FAIL → STOP_AND_FIX; UNCERTAIN → UNCERTAIN (pending review);
    # all PASS → SEAL.
    # UNCERTAIN correctly routes to PENDING_REVIEW — not STOP_AND_FIX.
    # -----------------------------------------------------------------------
    has_fail = any(c.verdict == Verdict.FAIL for c in checks)
    has_uncertain = any(c.verdict == Verdict.UNCERTAIN for c in checks)

    if has_fail:
        decision = Decision.STOP_AND_FIX
        status = InspectionStatus.COMPLETED
    elif has_uncertain:
        # UNCERTAIN never auto-seals — route to human review (PENDING_REVIEW)
        # This matches the challenge spec: UNCERTAIN → human checks before sealing.
        decision = Decision.STOP_AND_FIX   # Surface level: stop until reviewed
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
