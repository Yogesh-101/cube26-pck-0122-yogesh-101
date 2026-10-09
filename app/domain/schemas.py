"""
PCK Pack Manager — Domain Data Contracts (v2)

All Pydantic models that define the system's typed interfaces.
These schemas are the single source of truth for data flowing through
the pipeline: order input → VLM observation → decision engine → evidence record.

v2 additions:
  - SceneCoverage: captures whether the whole box interior is visible.
  - CheckKey.SCENE_COVERAGE: checks whether all items are visible in the scene.
  - CheckKey.PHOTO_REUSE: detects reused photos across different orders.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, field_validator, model_validator


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class Channel(str, Enum):
    """Supported sales channels. FBA is explicitly excluded (Amazon packs those)."""
    AMAZON_MFN = "amazon_mfn"
    SHOPIFY = "shopify"
    WALMART = "walmart"
    THREEPL_CLIENT = "3pl_client"


class Verdict(str, Enum):
    """Per-check verdict. UNCERTAIN is first-class, never a low-confidence PASS."""
    PASS_ = "pass"
    FAIL = "fail"
    UNCERTAIN = "uncertain"


class Decision(str, Enum):
    """Final operational decision for the package."""
    SEAL = "seal"
    STOP_AND_FIX = "stop_and_fix"


class InspectionStatus(str, Enum):
    """Status of the inspection record."""
    COMPLETED = "completed"
    PENDING = "pending"
    PENDING_REVIEW = "pending_review"


class CheckKey(str, Enum):
    """Named checks the system performs on each unit."""
    ITEMS_PRESENT = "items_present"
    QUANTITY_MATCH = "quantity_match"
    NO_EXTRA_ITEMS = "no_extra_items"
    NO_WRONG_ITEMS = "no_wrong_items"
    IMAGE_QUALITY = "image_quality"
    SCENE_COVERAGE = "scene_coverage"   # v2: was the whole box interior visible?
    PHOTO_REUSE = "photo_reuse"         # v2: was this exact photo already used for another order?


class UncertaintyReason(str, Enum):
    """Taxonomy of reasons evidence may be insufficient."""
    OCCLUSION = "occlusion"
    BLUR = "blur"
    POOR_LIGHTING = "poor_lighting"
    SIMILAR_PRODUCTS = "similar_products"
    CONFLICTING_IMAGES = "conflicting_images"
    INSUFFICIENT_VIEWS = "insufficient_views"
    CATALOGUE_GAP = "catalogue_gap"
    PARTIAL_VISIBILITY = "partial_visibility"
    MODEL_UNCERTAINTY = "model_uncertainty"


# ---------------------------------------------------------------------------
# Input Models
# ---------------------------------------------------------------------------

class OrderLine(BaseModel):
    """A single line in a customer order."""
    sku: str = Field(..., min_length=1, description="Product SKU identifier")
    quantity: int = Field(..., ge=1, description="Expected quantity for this SKU")


class Order(BaseModel):
    """Customer order to verify against the open package."""
    order_id: str = Field(..., min_length=1)
    unit_id: str = Field(..., min_length=1, description="Cross-manager join key (UNIT-xxxx)")
    org_id: str = Field(..., min_length=1, description="Tenant identifier for isolation")
    channel: Channel
    lines: list[OrderLine] = Field(..., min_length=1)

    @field_validator("channel", mode="before")
    @classmethod
    def reject_fba(cls, v: str) -> str:
        if isinstance(v, str) and v.lower() in ("fba", "amazon_fba"):
            raise ValueError("FBA orders are packed by Amazon — not in scope for Pack Manager")
        return v


class CatalogueProduct(BaseModel):
    """Product reference record from the seller's catalogue."""
    sku: str = Field(..., min_length=1)
    asin: Optional[str] = None
    name: str = Field(..., min_length=1)
    variant: Optional[str] = None
    description: Optional[str] = None
    attributes: dict[str, str] = Field(default_factory=dict)
    aliases: list[str] = Field(default_factory=list)
    # SKUs that look similar and must be discriminated (colour/size/variant).
    confusable_with: list[str] = Field(default_factory=list)
    # Parts that ship with this product but are also sold separately
    # (e.g. lamp includes a USB-C cable). Seeing one → UNCERTAIN, not FAIL.
    component_lookalikes: list[str] = Field(default_factory=list)
    reference_image_paths: list[str] = Field(default_factory=list)


class ImageInput(BaseModel):
    """A photograph of the open package, with quality metadata."""
    image_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    path: str = Field(..., min_length=1)
    sha256: Optional[str] = None
    blur_score: Optional[float] = None
    brightness: Optional[float] = None
    contrast: Optional[float] = None
    quality_engine: Optional[str] = None
    is_duplicate: bool = False


# ---------------------------------------------------------------------------
# VLM Output / Observation Models
# ---------------------------------------------------------------------------

class ObservedItem(BaseModel):
    """An item detected by the VLM in the open package."""
    sku: Optional[str] = Field(None, description="Matched SKU from catalogue, or null if unknown")
    name: str = Field(..., description="Product name as identified by the model")
    observed_quantity: int = Field(..., ge=0)
    confidence: float = Field(..., ge=0.0, le=1.0)
    evidence_image_ids: list[str] = Field(default_factory=list)
    observation_text: str = Field("", description="Model's textual observation for this item")


class UncertaintyDetail(BaseModel):
    """Structured uncertainty information — makes UNCERTAIN actionable."""
    reason_code: UncertaintyReason
    what_is_known: str
    what_is_unknown: str
    missing_evidence: str
    recommended_action: str


# ---------------------------------------------------------------------------
# Check & Evidence Models
# ---------------------------------------------------------------------------

class Check(BaseModel):
    """A single named verification check with its result."""
    check_key: CheckKey
    verdict: Verdict
    confidence: float = Field(..., ge=0.0, le=1.0)
    detail: str = Field("", description="Human-readable explanation of this check result")
    model_version: str = Field("", description="Model identifier used for this check")
    latency_ms: float = Field(0.0, ge=0.0)
    uncertainty: Optional[UncertaintyDetail] = None


class Outcome(BaseModel):
    """The final operational decision for the package."""
    decision: Decision
    decided_by: str = Field("decision_engine", description="'decision_engine' or operator_id")
    decided_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class HumanOverride(BaseModel):
    """Operator override — original AI decision is preserved, never overwritten."""
    original_decision: Decision
    new_decision: Decision
    reason: str = Field(..., min_length=1)
    operator_id: str = Field(..., min_length=1)
    overridden_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ImageRef(BaseModel):
    """Image reference within the evidence record."""
    image_id: str
    path: str
    sha256: Optional[str] = None
    captured_at: Optional[datetime] = None


class EvidenceRecord(BaseModel):
    """
    Official evidence contract — interoperable with Returns/Recovery Managers.
    Fields match the fixed contract from the organiser handbook.
    """
    record_id: str = Field(default_factory=lambda: f"PCK-{uuid.uuid4().hex[:12].upper()}")
    schema_version: str = Field("1.0.0")
    organization_id: str
    client_id: Optional[str] = None
    agent: str = Field("pack_manager")
    subject: str = Field("", description="unit_id this record is about")
    captured_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    operator_label: Optional[str] = None
    images: list[ImageRef] = Field(default_factory=list)
    checks: list[Check] = Field(default_factory=list)
    outcome: Optional[Outcome] = None
    overrides: list[HumanOverride] = Field(default_factory=list)
    status: InspectionStatus = InspectionStatus.PENDING
    content_hash: Optional[str] = None

    # Extended fields (beyond minimal contract, for our own traceability)
    order_id: Optional[str] = None
    observed_items: list[ObservedItem] = Field(default_factory=list)
    expected_lines: list[OrderLine] = Field(default_factory=list)
    uncertainties: list[UncertaintyDetail] = Field(default_factory=list)

    def compute_content_hash(self) -> str:
        """SHA-256 of the canonical JSON representation (excluding the hash itself)."""
        data = self.model_dump(mode="json", exclude={"content_hash"})
        canonical = json.dumps(data, sort_keys=True, default=str)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def finalize(self) -> "EvidenceRecord":
        """Set the content_hash. Call once before persisting."""
        self.content_hash = self.compute_content_hash()
        return self


# ---------------------------------------------------------------------------
# Inspection (top-level workflow object)
# ---------------------------------------------------------------------------

class Inspection(BaseModel):
    """Full inspection state: order + observations + checks + decision."""
    inspection_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    order: Order
    catalogue: list[CatalogueProduct] = Field(default_factory=list)
    images: list[ImageInput] = Field(default_factory=list)
    observed_items: list[ObservedItem] = Field(default_factory=list)
    checks: list[Check] = Field(default_factory=list)
    outcome: Optional[Outcome] = None
    status: InspectionStatus = InspectionStatus.PENDING
    evidence_record: Optional[EvidenceRecord] = None


# ---------------------------------------------------------------------------
# Discrepancy report (for STOP_AND_FIX detail)
# ---------------------------------------------------------------------------

class ItemDiscrepancy(BaseModel):
    """Per-item mismatch detail for operator display."""
    sku: Optional[str] = None
    product_name: str
    expected_quantity: int
    observed_quantity: int
    discrepancy_type: str = Field(
        ..., description="One of: missing, extra, wrong_quantity, wrong_item, unknown_item"
    )
    detail: str = ""


# ---------------------------------------------------------------------------
# v2: Scene Coverage (from VLM)
# ---------------------------------------------------------------------------

class SceneCoverage(BaseModel):
    """Assessment of whether the whole box interior is visible in the photos."""
    box_interior_fully_visible: bool = Field(
        True, description="True only if the entire inside of the box is in frame"
    )
    items_may_be_hidden: bool = Field(
        False, description="True if anything covers part of the box or items are stacked"
    )
    visibility_confidence: float = Field(
        1.0, ge=0.0, le=1.0,
        description="Confidence 0-1 that every item in the box is visible. Cluttered = below 0.5."
    )
    notes: str = Field("", description="Any scene-level observations")
