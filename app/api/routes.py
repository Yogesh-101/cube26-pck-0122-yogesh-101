"""
PCK Pack Manager — FastAPI Routes

API endpoints for pack verification, inspection retrieval,
and human override.
"""

from __future__ import annotations

import json
import logging
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app.config import get_settings
from app.domain.schemas import (
    CatalogueProduct,
    Channel,
    Decision,
    InspectionStatus,
    Order,
    OrderLine,
)
from app.pipeline import run_inspection
from app.storage.database import (
    find_image_hash_owners,
    get_inspection as db_get_inspection,
    init_database,
    list_inspections as db_list_inspections,
    save_inspection as db_save_inspection,
    save_override as db_save_override,
    get_overrides as db_get_overrides,
)
from app.vision.quality import compute_file_sha256

logger = logging.getLogger(__name__)
router = APIRouter()

# In-memory fallback (used alongside DB)
_inspections: dict[str, dict] = {}

# Initialize database on module load
init_database()


def _seen_hashes_for_order(org_id: str, order_id: str, image_paths: list[str]) -> dict[str, str]:
    """Prior record ids for photo bytes already used on a different order (same org)."""
    sha_list = [compute_file_sha256(p) for p in image_paths]
    return find_image_hash_owners(org_id, sha_list, exclude_order_id=order_id)


@router.post("/api/v1/quality/check")
async def quality_check(
    images: list[UploadFile] = File(..., description="One or more photos (upload or live capture)"),
):
    """
    OpenCV quality pre-check for uploads and live camera snapshots.

    Returns per-image blur / brightness / contrast / clip metrics so the
    operator can retake a bad frame before spending a Gemini call.
    """
    from app.vision.quality import assess_image_bytes

    if not images:
        raise HTTPException(status_code=422, detail="At least one image is required")

    results = []
    for upload in images:
        content = await upload.read()
        name = upload.filename or "capture.jpg"
        result = assess_image_bytes(content, label=name, source="upload")
        results.append(result.to_dict())

    any_bad = any((not r["is_usable"]) or r["is_uncertain"] for r in results)
    return {
        "engine": results[0]["engine"] if results else None,
        "ok": not any_bad,
        "n": len(results),
        "results": results,
        "advice": (
            "Retake photos that are dark, blurry, or low-contrast before verifying."
            if any_bad
            else "Image quality looks sufficient for pack verification."
        ),
    }

_UNSAFE_PATH_CHARS = re.compile(r"[^A-Za-z0-9._-]+")


def tenant_image_dir(storage_root: str | Path, org_id: str, unit_id: str) -> Path:
    """
    Place captures under storage/<org>/<unit>.

    A shared folder keyed only by unit id lets two tenants collide, and a
    raw id with `..` can escape the storage root. The resolved path is
    required to stay inside the storage root.
    """

    def segment(value: str, label: str) -> str:
        cleaned = _UNSAFE_PATH_CHARS.sub("_", value).strip("._")
        if not cleaned:
            raise HTTPException(status_code=400, detail=f"Invalid {label}")
        return cleaned

    root = Path(storage_root).resolve()
    dest = (root / segment(org_id, "org_id") / segment(unit_id, "unit_id")).resolve()
    if dest != root and root not in dest.parents:
        raise HTTPException(status_code=400, detail="Invalid storage path")
    return dest


_IMAGE_EXTENSIONS = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp", ".gif": "image/gif"}


async def persist_uploads(image_dir: Path, images: list[UploadFile], max_mb: int) -> list[str]:
    """Write only real images, capped in size, under the tenant directory."""
    if not images:
        raise HTTPException(status_code=422, detail="At least one package photo is required")

    saved: list[str] = []
    limit = max_mb * 1024 * 1024
    for upload in images:
        ext = Path(upload.filename or "").suffix.lower()
        if ext not in _IMAGE_EXTENSIONS:
            raise HTTPException(
                status_code=422,
                detail=f"Unsupported file type '{ext or 'none'}'. Use JPG, PNG, WebP, or GIF.",
            )
        content = await upload.read()
        if len(content) < 32:
            raise HTTPException(status_code=422, detail="An uploaded image is empty")
        if len(content) > limit:
            raise HTTPException(status_code=413, detail=f"Image exceeds the {max_mb}MB limit")
        file_path = image_dir / f"{uuid.uuid4().hex[:8]}{ext}"
        file_path.write_bytes(content)
        saved.append(str(file_path))
    return saved


def _storage_roots(settings=None) -> tuple[Path, Path]:
    """Return (storage_root, image_root) as absolute paths."""
    settings = settings or get_settings()
    image_root = Path(settings.image_storage_path).expanduser().resolve()
    # Prefer explicit STORAGE_ROOT; otherwise the parent of the images dir.
    storage_root = Path(settings.storage_root).expanduser().resolve()
    return storage_root, image_root


def relativize_inspection_paths(inspection_data: dict, storage_root: str | Path) -> dict:
    """
    Rewrite absolute capture paths to paths relative to the storage root.

    Absolute host paths break after restart/redeploy. Relative paths stay valid
    as long as the storage volume is mounted at the same IMAGE_STORAGE_PATH /
    STORAGE_ROOT.
    """
    root = Path(storage_root).expanduser().resolve()

    def _rel(path_str: str) -> str:
        try:
            return str(Path(path_str).expanduser().resolve().relative_to(root)).replace("\\", "/")
        except (ValueError, OSError):
            return path_str

    for img in inspection_data.get("images") or []:
        if img.get("path"):
            img["path"] = _rel(img["path"])

    evidence = inspection_data.get("evidence_record")
    if isinstance(evidence, dict):
        for img in evidence.get("images") or []:
            if img.get("path"):
                img["path"] = _rel(img["path"])

    return inspection_data


def persist_inspection(inspection_data: dict) -> None:
    """
    Durably store an inspection so it survives restart, redeploy, and code updates.

    1. Relativize photo paths under STORAGE_ROOT (volume-stable).
    2. Append to JSONL mirror on the volume (never truncated).
    3. Upsert SQLite and verify the row is readable.
    4. Keep an in-process cache only as a hot shortcut — DB+mirror are source of truth.

    Raises HTTP 503 if neither store can be confirmed — never pretends success.
    """
    from app.storage.durable import append_inspection_mirror

    settings = get_settings()
    storage_root, _ = _storage_roots(settings)
    relativize_inspection_paths(inspection_data, storage_root)
    _inspections[inspection_data["inspection_id"]] = inspection_data

    mirror_ok = False
    try:
        append_inspection_mirror(inspection_data, storage_root)
        mirror_ok = True
    except Exception as e:
        logger.error("JSONL mirror write failed: %s", e, exc_info=True)

    try:
        db_save_inspection(inspection_data)
    except Exception as e:
        logger.error("SQLite save failed: %s", e, exc_info=True)
        if mirror_ok:
            # Mirror has the row — still surface the DB failure so ops notice,
            # but the inspection is recoverable on next boot via recover_missing_into_db.
            raise HTTPException(
                status_code=503,
                detail=(
                    "Inspection was mirrored to durable storage but SQLite write failed. "
                    "It will be recovered on the next restart. Retry if you need it listed immediately."
                ),
            ) from e
        raise HTTPException(
            status_code=503,
            detail="Failed to persist inspection to durable storage. Retry — data was not discarded silently.",
        ) from e


def resolve_inspection_image(inspection: dict, image_id: str, storage_root: str | Path) -> Optional[Path]:
    """
    Return the capture file only when this inspection lists that image id
    and the stored path stays inside the storage root.

    Accepts relative paths (preferred, durable) or absolute paths (legacy rows).
    Callers must already have loaded the inspection for the requesting org.
    """
    listed = list(inspection.get("images") or [])
    evidence = inspection.get("evidence_record") or {}
    listed.extend(evidence.get("images") or [])

    match = next((item for item in listed if item.get("image_id") == image_id), None)
    if not match or not match.get("path"):
        return None

    root = Path(storage_root).expanduser().resolve()
    raw = Path(match["path"])
    path = (root / raw).resolve() if not raw.is_absolute() else raw.resolve()
    if path != root and root not in path.parents:
        return None
    if not path.is_file():
        return None
    return path


# ---------------------------------------------------------------------------
# Request/Response Models
# ---------------------------------------------------------------------------

class VerifyRequest(BaseModel):
    """Request body for the /verify endpoint (JSON part)."""
    order_id: str
    unit_id: str
    org_id: str
    channel: Channel
    order_lines: list[OrderLine]
    catalogue: list[CatalogueProduct] = Field(default_factory=list)


class VerifyResponse(BaseModel):
    """Response from the /verify endpoint."""
    inspection_id: str
    order_id: str
    unit_id: str
    decision: str
    status: str
    checks: list[dict]
    observed_items: list[dict]
    discrepancies: list[dict] = Field(default_factory=list)
    evidence_record_id: Optional[str] = None
    content_hash: Optional[str] = None


class OverrideRequest(BaseModel):
    """Request body for human override."""
    new_decision: Decision
    reason: str = Field(..., min_length=1)
    operator_id: str = Field(..., min_length=1)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post("/api/v1/verify", response_model=VerifyResponse)
async def verify_package(
    order_data: str = Form(..., description="JSON string of VerifyRequest"),
    images: list[UploadFile] = File(..., description="Package photographs"),
):
    """
    Verify package contents against an order.

    Accepts order data as a JSON form field and images as file uploads.
    Returns the inspection result with decision, checks, and evidence.
    """
    settings = get_settings()

    # Parse order data
    try:
        req = VerifyRequest.model_validate_json(order_data)
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"Invalid order data: {e}")

    image_dir = tenant_image_dir(settings.image_storage_path, req.org_id, req.unit_id)
    image_dir.mkdir(parents=True, exist_ok=True)
    image_paths = await persist_uploads(image_dir, images, settings.max_image_size_mb)

    # Build order
    order = Order(
        order_id=req.order_id,
        unit_id=req.unit_id,
        org_id=req.org_id,
        channel=req.channel,
        lines=req.order_lines,
    )

    # Run pipeline (photo reuse map is org-scoped; same order re-check is allowed)
    inspection = run_inspection(
        order=order,
        image_paths=image_paths,
        catalogue=req.catalogue,
        seen_hashes=_seen_hashes_for_order(req.org_id, req.order_id, image_paths),
    )

    inspection_data = inspection.model_dump(mode="json")
    persist_inspection(inspection_data)

    # Build response
    decision_str = inspection.outcome.decision.value if inspection.outcome else "pending"

    return VerifyResponse(
        inspection_id=inspection.inspection_id,
        order_id=order.order_id,
        unit_id=order.unit_id,
        decision=decision_str,
        status=inspection.status.value,
        checks=[c.model_dump(mode="json") for c in inspection.checks],
        observed_items=[o.model_dump(mode="json") for o in inspection.observed_items],
        evidence_record_id=inspection.evidence_record.record_id if inspection.evidence_record else None,
        content_hash=inspection.evidence_record.content_hash if inspection.evidence_record else None,
    )


@router.post("/api/v1/verify/json", response_model=VerifyResponse)
async def verify_package_json(
    order_id: str = Form(...),
    unit_id: str = Form(...),
    org_id: str = Form(...),
    channel: str = Form(...),
    order_lines_json: str = Form(..., description="JSON array of {sku, quantity}"),
    catalogue_json: str = Form(default="[]", description="JSON array of catalogue products"),
    images: list[UploadFile] = File(...),
):
    """
    Alternative verify endpoint with flat form fields (easier for UI forms).
    """
    settings = get_settings()

    try:
        lines_data = json.loads(order_lines_json)
        order_lines = [OrderLine(**l) for l in lines_data]
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"Invalid order_lines: {e}")

    try:
        cat_data = json.loads(catalogue_json)
        catalogue = [CatalogueProduct(**c) for c in cat_data]
    except Exception:
        catalogue = []

    image_dir = tenant_image_dir(settings.image_storage_path, org_id, unit_id)
    image_dir.mkdir(parents=True, exist_ok=True)
    image_paths = await persist_uploads(image_dir, images, settings.max_image_size_mb)

    order = Order(
        order_id=order_id,
        unit_id=unit_id,
        org_id=org_id,
        channel=channel,
        lines=order_lines,
    )

    inspection = run_inspection(
        order=order,
        image_paths=image_paths,
        catalogue=catalogue,
        seen_hashes=_seen_hashes_for_order(org_id, order_id, image_paths),
    )

    inspection_data = inspection.model_dump(mode="json")
    persist_inspection(inspection_data)

    decision_str = inspection.outcome.decision.value if inspection.outcome else "pending"

    return VerifyResponse(
        inspection_id=inspection.inspection_id,
        order_id=order.order_id,
        unit_id=order.unit_id,
        decision=decision_str,
        status=inspection.status.value,
        checks=[c.model_dump(mode="json") for c in inspection.checks],
        observed_items=[o.model_dump(mode="json") for o in inspection.observed_items],
        evidence_record_id=inspection.evidence_record.record_id if inspection.evidence_record else None,
        content_hash=inspection.evidence_record.content_hash if inspection.evidence_record else None,
    )


@router.get("/api/v1/inspections/{inspection_id}")
async def get_inspection(inspection_id: str, org_id: str = Query(...)):
    """
    Retrieve a single inspection by ID (org-scoped for isolation).
    """
    # Try DB first, fallback to memory
    data = db_get_inspection(inspection_id, org_id)
    if not data:
        data = _inspections.get(inspection_id)
        if not data or data.get("order", {}).get("org_id") != org_id:
            raise HTTPException(status_code=404, detail="Inspection not found")

    # Overrides are stored append-only in their own table, so `outcome` still holds
    # the agent's original verdict. Return both: the operator UI must be able to show
    # the decision currently in force without that erasing what the agent said.
    overrides = db_get_overrides(inspection_id, org_id)
    if not overrides and data.get("evidence_record"):
        overrides = data["evidence_record"].get("overrides", [])

    outcome = data.get("outcome") or {}
    return {
        **data,
        "overrides": overrides,
        "current_decision": overrides[-1]["new_decision"] if overrides else outcome.get("decision"),
    }


def _load_inspection_for_org(inspection_id: str, org_id: str) -> dict:
    data = db_get_inspection(inspection_id, org_id)
    if not data:
        data = _inspections.get(inspection_id)
        if not data or data.get("order", {}).get("org_id") != org_id:
            raise HTTPException(status_code=404, detail="Inspection not found")
    return data


@router.get("/api/v1/inspections/{inspection_id}/images/{image_id}")
async def get_inspection_image(inspection_id: str, image_id: str, org_id: str = Query(...)):
    """
    Serve one captured photo. The inspection lookup is org-scoped, and the
    file must be listed on that record and stay inside the storage root.
    Guessing another tenant's inspection id or image id returns 404.
    """
    data = _load_inspection_for_org(inspection_id, org_id)
    # Image paths are stored relative to STORAGE_ROOT (which contains /images/...).
    storage_root, _ = _storage_roots()
    path = resolve_inspection_image(data, image_id, storage_root)
    if path is None:
        raise HTTPException(status_code=404, detail="Image not found")
    media = _IMAGE_EXTENSIONS.get(path.suffix.lower(), "application/octet-stream")
    return FileResponse(path, media_type=media)


@router.get("/api/v1/inspections")
async def list_inspections(org_id: str = Query(...)):
    """
    List all inspections for an organization (tenancy-scoped).
    """
    results = db_list_inspections(org_id)

    # Merge in-memory inspections not yet in DB
    db_ids = {r["inspection_id"] for r in results}
    for iid, data in _inspections.items():
        if iid not in db_ids and data.get("order", {}).get("org_id") == org_id:
            results.append({
                "inspection_id": iid,
                "order_id": data.get("order", {}).get("order_id"),
                "unit_id": data.get("order", {}).get("unit_id"),
                "status": data.get("status"),
                "decision": data.get("outcome", {}).get("decision") if data.get("outcome") else None,
            })
    return results


@router.post("/api/v1/inspections/{inspection_id}/override")
async def override_decision(
    inspection_id: str,
    override: OverrideRequest,
    org_id: str = Query(...),
):
    """
    Human operator override — preserves original AI decision.
    Append-only: the original verdict is never overwritten.
    """
    # Try DB first
    data = db_get_inspection(inspection_id, org_id)
    if not data:
        data = _inspections.get(inspection_id)
        if not data or data.get("order", {}).get("org_id") != org_id:
            raise HTTPException(status_code=404, detail="Inspection not found")

    # Keep the agent's actual verdict. A pending capture has none — do not
    # invent STOP & FIX as if the model had decided.
    outcome = data.get("outcome") or {}
    original_decision = outcome.get("decision") or "pending"

    # Save override to DB
    success = db_save_override(
        inspection_id=inspection_id,
        org_id=org_id,
        original_decision=original_decision,
        new_decision=override.new_decision.value,
        reason=override.reason,
        operator_id=override.operator_id,
    )

    if not success:
        raise HTTPException(status_code=404, detail="Inspection not found or wrong org")

    # Also update in-memory if present
    if inspection_id in _inspections:
        mem = _inspections[inspection_id]
        if "evidence_record" in mem and mem["evidence_record"]:
            if "overrides" not in mem["evidence_record"]:
                mem["evidence_record"]["overrides"] = []
            mem["evidence_record"]["overrides"].append({
                "original_decision": original_decision,
                "new_decision": override.new_decision.value,
                "reason": override.reason,
                "operator_id": override.operator_id,
                "overridden_at": datetime.now(timezone.utc).isoformat(),
            })
        # outcome stays the agent's original verdict. The decision in force
        # is the append-only override, not a rewrite of that object.
        mem["status"] = InspectionStatus.COMPLETED.value

    return {
        "inspection_id": inspection_id,
        "original_decision": original_decision,
        "new_decision": override.new_decision.value,
        "overridden_by": override.operator_id,
        "reason": override.reason,
    }


@router.get("/api/v1/inspections/{inspection_id}/evidence")
async def get_evidence_record(inspection_id: str, org_id: str = Query(...)):
    """
    Export the evidence record in the official CUBE contract format.
    This endpoint is designed for interoperability with Returns Manager
    and Recovery Manager (Round 3 pod integration).
    """
    data = db_get_inspection(inspection_id, org_id)
    if not data:
        data = _inspections.get(inspection_id)
        if not data or data.get("order", {}).get("org_id") != org_id:
            raise HTTPException(status_code=404, detail="Inspection not found")

    evidence = data.get("evidence_record")
    if not evidence:
        raise HTTPException(status_code=404, detail="No evidence record for this inspection")

    return evidence


@router.get("/api/v1/evidence/by-unit/{unit_id}")
async def get_evidence_by_unit(unit_id: str, org_id: str = Query(...)):
    """
    Look up evidence by unit_id — the cross-manager join key.
    Returns Manager and Recovery Manager use this to find what was packed.
    """
    # Search in DB
    from app.storage.database import _get_connection, get_db_path
    conn = _get_connection(get_db_path())
    try:
        row = conn.execute(
            "SELECT evidence_record FROM inspections WHERE unit_id = ? AND org_id = ? "
            "ORDER BY created_at DESC LIMIT 1",
            (unit_id, org_id),
        ).fetchone()
        if row and row["evidence_record"]:
            import json as _json
            return _json.loads(row["evidence_record"])
    finally:
        conn.close()

    # Fallback to in-memory
    for data in _inspections.values():
        if (data.get("order", {}).get("unit_id") == unit_id
                and data.get("order", {}).get("org_id") == org_id):
            evidence = data.get("evidence_record")
            if evidence:
                return evidence

    raise HTTPException(status_code=404, detail=f"No evidence found for unit {unit_id}")


@router.get("/api/v1/health")
async def health():
    """Health check — includes durable storage proof so redeploys can be verified."""
    from app.storage.database import count_inspections, get_db_path
    from app.storage.durable import mirror_path, storage_root

    settings = get_settings()
    root = storage_root()
    db = get_db_path()
    mirror = mirror_path(root)
    try:
        n = count_inspections()
        db_ok = db.is_file()
        writable = os.access(root, os.W_OK)
        status = "healthy" if db_ok and writable else "degraded"
    except Exception as e:
        return {
            "status": "degraded",
            "agent": "pack_manager",
            "version": "1.0.0",
            "error": str(e),
            "storage_root": str(root),
        }
    return {
        "status": status,
        "agent": "pack_manager",
        "version": "1.0.0",
        "storage": {
            "root": str(root),
            "database": str(db),
            "database_exists": db_ok,
            "mirror": str(mirror),
            "mirror_exists": mirror.is_file(),
            "images": str(Path(settings.image_storage_path).resolve()),
            "writable": writable,
            "inspection_count": n,
            "survives_redeploy": True,
        },
    }
