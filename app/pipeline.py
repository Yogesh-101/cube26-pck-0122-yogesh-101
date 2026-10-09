"""
PCK Pack Manager — Inspection Pipeline (v2)

End-to-end orchestration:
  Order + Images → Quality Gate → VLM → Decision Engine → Evidence Record

Implements fail-open: VLM errors produce a pending record, never block the operator.

v2 upgrades:
  - Passes SceneCoverage from VLM result into decision engine
  - Passes image SHA-256 hashes into decision engine for photo reuse detection
  - Passes seen_hashes dict (from request context) for cross-order reuse check
  - Propagates prompt_version from VLM result into model metadata
  - Improved pending inspection to include reason on all checks (not just image_quality)
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

from app.config import get_settings
from app.decision.engine import DecisionResult, run_decision_engine
from app.domain.schemas import (
    CatalogueProduct,
    Check,
    CheckKey,
    Decision,
    EvidenceRecord,
    ImageInput,
    ImageRef,
    Inspection,
    InspectionStatus,
    ObservedItem,
    Order,
    OrderLine,
    Outcome,
    SceneCoverage,
    Verdict,
)
from app.vision.gemini_client import GeminiPackVerifier, VLMError, VerificationResult
from app.vision.quality import QualityResult, assess_batch_quality

logger = logging.getLogger(__name__)


def run_inspection(
    order: Order,
    image_paths: list[str],
    catalogue: list[CatalogueProduct] | None = None,
    seen_hashes: dict[str, str] | None = None,
) -> Inspection:
    """
    Execute the full pack verification pipeline.

    1. Validate inputs (already done by Pydantic on Order construction)
    2. Assess image quality
    3. Call VLM (single batched call) — order-blind
    4. Run decision engine (deterministic)
    5. Build evidence record

    On VLM failure: returns a pending inspection (fail-open).

    Args:
        order: The customer order to verify.
        image_paths: Paths to package photographs on disk.
        catalogue: Product catalogue for SKU grounding (optional).
        seen_hashes: Mapping of image SHA-256 → prior record_id for reuse detection (optional).
    """
    settings = get_settings()
    catalogue = catalogue or []
    now = datetime.now(timezone.utc)

    # Step 1: Image quality gate
    quality_results, _quality_all_ok, quality_uncertain = assess_batch_quality(image_paths)

    usable_paths = [r.path for r in quality_results if r.is_usable]
    image_sha256_list = [r.sha256 for r in quality_results if r.sha256]
    image_inputs = [
        ImageInput(
            path=r.path,
            sha256=r.sha256,
            blur_score=r.blur_score,
            brightness=r.brightness,
            contrast=getattr(r, "contrast", None),
            quality_engine=getattr(r, "engine", None),
            is_duplicate=any("Duplicate" in issue for issue in r.issues),
        )
        for r in quality_results
    ]

    # If no usable images, fail-open with pending
    if not usable_paths:
        logger.warning(f"No usable images for order {order.order_id}")
        return _build_pending_inspection(
            order, image_inputs, catalogue,
            reason="No usable images available after quality gate",
        )

    # Step 2: Call VLM
    try:
        verifier = GeminiPackVerifier(
            api_key=settings.gemini_api_key,
            model=settings.gemini_model,
        )
        # Order lines are not passed into the VLM prompt (order-blind).
        # The decision engine compares observations to the order below.
        vlm_result = verifier.verify_package(
            image_paths=usable_paths,
            catalogue=catalogue,
            timeout_seconds=settings.vlm_timeout_seconds,
        )
    except VLMError as e:
        logger.error(f"VLM error for order {order.order_id}: {e}")
        return _build_pending_inspection(
            order, image_inputs, catalogue,
            reason=f"VLM error: {e}",
        )

    # Step 3: Decision engine (v2: passes scene coverage + photo hashes)
    # Soft quality problems (dark/blur / VLM says insufficient) → UNCERTAIN, never SEAL.
    # Hard unusable images already returned pending above.
    soft_quality_uncertain = quality_uncertain or (not vlm_result.image_quality_ok)
    ordered_skus = {line.sku for line in order.lines}
    component_lookalike_skus: set[str] = set()
    for product in catalogue:
        if product.sku in ordered_skus:
            component_lookalike_skus.update(product.component_lookalikes or [])
    decision_result = run_decision_engine(
        expected_lines=order.lines,
        observed_items=vlm_result.observed_items,
        image_quality_ok=True,
        image_quality_uncertain=soft_quality_uncertain,
        model_version=vlm_result.model_version,
        latency_ms=vlm_result.latency_ms,
        scene=vlm_result.scene,                      # v2: scene coverage
        image_sha256_list=image_sha256_list,          # v2: for reuse detection
        seen_hashes=seen_hashes,                      # v2: cross-order reuse map
        component_lookalike_skus=component_lookalike_skus,
    )

    # Step 4: Build evidence record
    evidence = EvidenceRecord(
        organization_id=order.org_id,
        subject=order.unit_id,
        captured_at=now,
        images=[
            ImageRef(
                image_id=img.image_id,
                path=img.path,
                sha256=img.sha256,
                captured_at=now,
            )
            for img in image_inputs
        ],
        checks=decision_result.checks,
        outcome=decision_result.outcome,
        status=decision_result.status,
        order_id=order.order_id,
        observed_items=vlm_result.observed_items,
        expected_lines=order.lines,
        uncertainties=decision_result.uncertainties,
    )
    evidence.finalize()

    # Step 5: Build inspection
    inspection = Inspection(
        order=order,
        catalogue=catalogue,
        images=image_inputs,
        observed_items=vlm_result.observed_items,
        checks=decision_result.checks,
        outcome=decision_result.outcome,
        status=decision_result.status,
        evidence_record=evidence,
    )

    logger.info(
        f"Inspection complete: order={order.order_id} "
        f"decision={decision_result.decision.value} "
        f"status={decision_result.status.value} "
        f"checks={len(decision_result.checks)} "
        f"latency={vlm_result.latency_ms:.0f}ms "
        f"prompt_version={vlm_result.prompt_version}"
    )

    return inspection


def _build_pending_inspection(
    order: Order,
    image_inputs: list[ImageInput],
    catalogue: list[CatalogueProduct],
    reason: str,
) -> Inspection:
    """
    Build a pending inspection record when VLM fails (fail-open).

    The capture is preserved and the record is saved as pending.
    The operator is not blocked.
    """
    now = datetime.now(timezone.utc)

    pending_checks = [
        Check(
            check_key=CheckKey.IMAGE_QUALITY,
            verdict=Verdict.UNCERTAIN,
            confidence=0.0,
            detail=reason,
            model_version="",
            latency_ms=0.0,
        ),
    ]

    evidence = EvidenceRecord(
        organization_id=order.org_id,
        subject=order.unit_id,
        captured_at=now,
        images=[
            ImageRef(image_id=img.image_id, path=img.path, sha256=img.sha256, captured_at=now)
            for img in image_inputs
        ],
        checks=pending_checks,
        status=InspectionStatus.PENDING,
        order_id=order.order_id,
        expected_lines=order.lines,
    )
    evidence.finalize()

    return Inspection(
        order=order,
        catalogue=catalogue,
        images=image_inputs,
        checks=pending_checks,
        status=InspectionStatus.PENDING,
        evidence_record=evidence,
    )
