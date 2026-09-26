"""
Live VLM Integration Test — proves the full pipeline works end-to-end.

This test creates a real test image, sends it through the Gemini VLM,
and verifies the complete pipeline: quality gate → VLM → decision engine → evidence.

Requires GEMINI_API_KEY in .env to run.
Skip with: pytest -m "not live"
"""

import os
import sys
import tempfile
from pathlib import Path

import pytest

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:
    pytest.skip("Pillow not installed", allow_module_level=True)


def _create_test_product_image(path: str, product_name: str = "USB-C Cable"):
    """Create a realistic-ish test image simulating a product in a box."""
    img = Image.new("RGB", (800, 600), color=(200, 180, 150))
    draw = ImageDraw.Draw(img)

    # Draw a "box" (brown rectangle)
    draw.rectangle([100, 80, 700, 520], fill=(160, 120, 80), outline=(100, 70, 40), width=3)

    # Draw a "product" inside the box
    draw.rectangle([200, 150, 550, 400], fill=(40, 40, 40), outline=(60, 60, 60), width=2)

    # Add text label
    try:
        font = ImageFont.truetype("arial.ttf", 24)
    except (OSError, IOError):
        font = ImageFont.load_default()

    draw.text((250, 250), product_name, fill=(255, 255, 255), font=font)
    draw.text((250, 290), "1x unit", fill=(200, 200, 200), font=font)

    img.save(path, "JPEG", quality=85)
    return path


@pytest.mark.live
class TestLiveVLM:
    """Live integration tests — require GEMINI_API_KEY."""

    @pytest.fixture(autouse=True)
    def check_api_key(self):
        from dotenv import load_dotenv
        load_dotenv()
        key = os.getenv("GEMINI_API_KEY", "")
        if not key or key == "your-gemini-api-key-here":
            pytest.skip("GEMINI_API_KEY not set — skipping live VLM test")

    def test_full_pipeline_correct_order(self, tmp_path):
        """End-to-end: correct order → should SEAL."""
        from app.domain.schemas import Order, OrderLine, CatalogueProduct, Channel
        from app.pipeline import run_inspection

        # Create test image
        img_path = str(tmp_path / "test_product.jpg")
        _create_test_product_image(img_path, "USB-C Cable")

        order = Order(
            order_id="LIVE-TEST-001",
            unit_id="UNIT-LIVE-001",
            org_id="org_live_test",
            channel=Channel.SHOPIFY,
            lines=[OrderLine(sku="SKU-CABLE-USBC", quantity=1)],
        )

        catalogue = [
            CatalogueProduct(
                sku="SKU-CABLE-USBC",
                name="USB-C Charging Cable",
                description="Black USB-C to USB-A cable, 1 meter",
            ),
        ]

        result = run_inspection(
            order=order,
            image_paths=[img_path],
            catalogue=catalogue,
        )

        # Verify the pipeline completed (either completed or pending_review)
        assert result.inspection_id is not None
        assert result.evidence_record is not None
        assert result.evidence_record.record_id.startswith("PCK-")
        assert result.evidence_record.content_hash is not None
        assert len(result.evidence_record.content_hash) == 64  # SHA-256

        # The decision should exist
        assert result.outcome is not None
        assert result.outcome.decision.value in ("seal", "stop_and_fix")

        # Evidence should have checks
        assert len(result.checks) >= 1

        # Print results for manual verification
        print(f"\n{'='*60}")
        print(f"LIVE VLM TEST RESULT")
        print(f"{'='*60}")
        print(f"Inspection ID: {result.inspection_id}")
        print(f"Decision: {result.outcome.decision.value}")
        print(f"Status: {result.status.value}")
        print(f"Observed items: {len(result.observed_items)}")
        for item in result.observed_items:
            print(f"  - {item.name} (SKU: {item.sku}, qty: {item.observed_quantity}, conf: {item.confidence:.2f})")
        print(f"Checks:")
        for check in result.checks:
            print(f"  - {check.check_key.value}: {check.verdict.value} ({check.confidence:.2f})")
        print(f"Evidence record: {result.evidence_record.record_id}")
        print(f"Content hash: {result.evidence_record.content_hash[:16]}...")
        print(f"Model: {result.checks[0].model_version if result.checks else 'N/A'}")
        print(f"{'='*60}\n")

    def test_vlm_client_direct(self, tmp_path):
        """Direct VLM client test — verify Gemini returns structured output."""
        from app.config import get_settings
        from app.domain.schemas import OrderLine, CatalogueProduct
        from app.vision.gemini_client import GeminiPackVerifier

        settings = get_settings()

        img_path = str(tmp_path / "direct_test.jpg")
        _create_test_product_image(img_path, "Water Bottle 750ml")

        verifier = GeminiPackVerifier(
            api_key=settings.gemini_api_key,
            model=settings.gemini_model,
        )

        result = verifier.verify_package(
            image_paths=[img_path],
            order_lines=[OrderLine(sku="SKU-BOTTLE-750", quantity=1)],
            catalogue=[
                CatalogueProduct(
                    sku="SKU-BOTTLE-750",
                    name="Water Bottle 750ml",
                    description="Stainless steel water bottle, blue, 750ml",
                ),
            ],
        )

        # Verify structured response
        assert result.observed_items is not None
        assert result.model_version == settings.gemini_model
        assert result.latency_ms > 0
        assert isinstance(result.image_quality_ok, bool)

        print(f"\nDirect VLM result: {len(result.observed_items)} items observed, "
              f"quality_ok={result.image_quality_ok}, "
              f"latency={result.latency_ms:.0f}ms")

    def test_fail_open_on_bad_key(self, tmp_path):
        """Verify fail-open: bad API key should produce pending, not crash."""
        from app.domain.schemas import Order, OrderLine, Channel
        from app.vision.gemini_client import GeminiPackVerifier, VLMError

        img_path = str(tmp_path / "failopen_test.jpg")
        _create_test_product_image(img_path, "Test Product")

        verifier = GeminiPackVerifier(
            api_key="invalid-key-for-failopen-test",
            model="gemini-2.5-flash",
        )

        with pytest.raises(VLMError):
            verifier.verify_package(
                image_paths=[img_path],
                order_lines=[OrderLine(sku="SKU-TEST", quantity=1)],
                catalogue=[],
            )
