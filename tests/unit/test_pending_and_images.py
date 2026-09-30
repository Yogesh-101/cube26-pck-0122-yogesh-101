"""
Fail-open pending records must explain themselves, and image files from
one organisation must not land in another organisation's folder.
"""

from fastapi import HTTPException

from app.api.routes import tenant_image_dir
from app.domain.schemas import Channel, InspectionStatus, Order, OrderLine, Verdict
from app.pipeline import _build_pending_inspection


def _order() -> Order:
    return Order(
        order_id="ORD-PEND",
        unit_id="UNIT-PEND",
        org_id="org_demo_alpha",
        channel=Channel.SHOPIFY,
        lines=[OrderLine(sku="SKU-001", quantity=1)],
    )


def test_pending_reason_is_on_the_inspection_and_the_evidence():
    reason = "VLM error: 503 UNAVAILABLE. high demand"
    inspection = _build_pending_inspection(_order(), [], [], reason=reason)

    assert inspection.status == InspectionStatus.PENDING
    assert inspection.outcome is None
    assert len(inspection.checks) == 1
    assert inspection.checks[0].verdict == Verdict.UNCERTAIN
    assert inspection.checks[0].detail == reason
    assert inspection.evidence_record is not None
    assert inspection.evidence_record.checks[0].detail == reason


def test_image_dir_is_scoped_to_org(tmp_path):
    alpha = tenant_image_dir(tmp_path, "org_demo_alpha", "UNIT-0001")
    bravo = tenant_image_dir(tmp_path, "org_demo_bravo", "UNIT-0001")

    assert alpha != bravo
    assert alpha.parent.name == "org_demo_alpha"
    assert bravo.parent.name == "org_demo_bravo"
    assert alpha.is_relative_to(tmp_path.resolve())


def test_resolve_image_stays_inside_storage_and_on_this_record(tmp_path):
    from app.api.routes import resolve_inspection_image

    photo = tmp_path / "org_demo_alpha" / "UNIT-0001"
    photo.mkdir(parents=True)
    file_path = photo / "shot.png"
    file_path.write_bytes(b"\x89PNG\r\n")

    outside = tmp_path.parent / "escaped.png"
    outside.write_bytes(b"nope")

    inspection = {
        "images": [],
        "evidence_record": {
            "images": [
                {"image_id": "img-ok", "path": str(file_path)},
                {"image_id": "img-escape", "path": str(outside)},
            ]
        },
    }

    found = resolve_inspection_image(inspection, "img-ok", tmp_path)
    assert found == file_path.resolve()
    assert resolve_inspection_image(inspection, "img-escape", tmp_path) is None
    assert resolve_inspection_image(inspection, "does-not-exist", tmp_path) is None
    outside.unlink(missing_ok=True)


def test_image_dir_rejects_path_escape(tmp_path):
    try:
        dest = tenant_image_dir(tmp_path, "..", "UNIT-0001")
    except HTTPException as exc:
        assert exc.status_code == 400
        return

    assert dest.is_relative_to(tmp_path.resolve())
    assert ".." not in dest.parts
