"""
PCK Pack Manager — Gemini VLM Client

Single batched model call per unit carrying all checks (Engineering Rule 2).
Accepts all package images + order + catalogue context in one request.
Returns structured JSON matching our Pydantic schema.
Implements fail-open: errors produce a pending record, never block the operator.
"""

from __future__ import annotations

import base64
import json
import logging
import time
from pathlib import Path
from typing import Any, Optional

from app.domain.schemas import (
    CatalogueProduct,
    ObservedItem,
    OrderLine,
    UncertaintyDetail,
    UncertaintyReason,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# VLM Response Schema (what we ask Gemini to return)
# ---------------------------------------------------------------------------

VLM_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "observed_items": {
            "type": "array",
            "description": "Every distinct product visible in the open package",
            "items": {
                "type": "object",
                "properties": {
                    "sku": {
                        "type": ["string", "null"],
                        "description": "Matched SKU from the provided catalogue, or null if no match"
                    },
                    "name": {
                        "type": "string",
                        "description": "Product name as identified"
                    },
                    "observed_quantity": {
                        "type": "integer",
                        "description": "Count of this product visible in the package"
                    },
                    "confidence": {
                        "type": "number",
                        "description": "Confidence in identification (0.0 to 1.0)"
                    },
                    "observation_text": {
                        "type": "string",
                        "description": "Brief description of what was observed"
                    }
                },
                "required": ["name", "observed_quantity", "confidence", "observation_text"]
            }
        },
        "image_quality_assessment": {
            "type": "object",
            "properties": {
                "is_sufficient": {
                    "type": "boolean",
                    "description": "Whether the images are clear enough for reliable verification"
                },
                "issues": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Any quality issues noticed (blur, glare, occlusion, etc.)"
                }
            },
            "required": ["is_sufficient", "issues"]
        },
        "overall_notes": {
            "type": "string",
            "description": "Any additional observations about the package contents"
        }
    },
    "required": ["observed_items", "image_quality_assessment", "overall_notes"]
}


def _build_prompt(
    order_lines: list[OrderLine],
    catalogue: list[CatalogueProduct],
) -> str:
    """Build the system+user prompt for the VLM call."""

    # Format expected order
    order_text = "\n".join(
        f"  - SKU: {line.sku}, Quantity: {line.quantity}"
        for line in order_lines
    )

    # Format catalogue for grounding
    catalogue_text = "\n".join(
        f"  - SKU: {p.sku} | Name: {p.name}"
        + (f" | Variant: {p.variant}" if p.variant else "")
        + (f" | Description: {p.description}" if p.description else "")
        for p in catalogue
    ) if catalogue else "  No catalogue provided — identify products by visual appearance."

    return f"""You are a Pack Manager verification agent. You are examining photographs of an open package before it is sealed for shipping.

TASK: Identify every item visible in the open package and count quantities. Match items to the product catalogue provided.

EXPECTED ORDER:
{order_text}

PRODUCT CATALOGUE:
{catalogue_text}

RULES:
1. Only report what you can actually see in the images. Never invent items.
2. Match visible items to catalogue SKUs where possible. If no reliable match, set sku to null.
3. Count each distinct physical item exactly once — do not double-count items visible in multiple images.
4. If an item is partially occluded or hard to identify, lower the confidence score.
5. Report confidence between 0.0 (no idea) and 1.0 (certain).
6. Use confidence below 0.5 when identification is genuinely uncertain.
7. If image quality prevents reliable verification, mark is_sufficient as false.
8. Do NOT assume hidden items exist — report only what is visible.

Examine the provided images and return your structured analysis."""


def _load_image_as_part(path: str) -> dict[str, Any]:
    """Load an image file and return it as a Gemini API inline_data part."""
    file_path = Path(path)
    suffix = file_path.suffix.lower()
    mime_map = {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".webp": "image/webp",
        ".gif": "image/gif",
    }
    mime_type = mime_map.get(suffix, "image/jpeg")

    with open(path, "rb") as f:
        image_data = f.read()

    return {
        "inline_data": {
            "mime_type": mime_type,
            "data": base64.b64encode(image_data).decode("utf-8"),
        }
    }


class GeminiPackVerifier:
    """
    Gemini-based pack verification client.

    Makes a single batched call per unit with all images + context.
    Returns parsed ObservedItems or raises on failure for fail-open handling.
    """

    def __init__(self, api_key: str, model: str = "gemini-2.5-flash"):
        self.api_key = api_key
        self.model = model
        self._client = None

    def _get_client(self):
        """Lazy-init the Gemini client."""
        if self._client is None:
            from google import genai
            self._client = genai.Client(api_key=self.api_key)
        return self._client

    def verify_package(
        self,
        image_paths: list[str],
        order_lines: list[OrderLine],
        catalogue: list[CatalogueProduct],
        timeout_seconds: int = 60,
    ) -> VerificationResult:
        """
        Single batched VLM call for pack verification.

        Args:
            image_paths: Paths to package photographs.
            order_lines: Expected order lines.
            catalogue: Product catalogue for grounding.
            timeout_seconds: Max wait time for model response.

        Returns:
            VerificationResult with observed items, quality info, and metadata.

        Raises:
            VLMError: On any failure (caller implements fail-open).
        """
        start_time = time.time()

        try:
            client = self._get_client()

            # Build prompt
            prompt_text = _build_prompt(order_lines, catalogue)

            # Build content parts: text prompt + all images
            from google.genai import types

            parts = [types.Part.from_text(text=prompt_text)]

            for img_path in image_paths:
                try:
                    img_bytes = Path(img_path).read_bytes()
                    suffix = Path(img_path).suffix.lower()
                    mime_map = {
                        ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                        ".png": "image/png", ".webp": "image/webp",
                    }
                    mime = mime_map.get(suffix, "image/jpeg")
                    parts.append(types.Part.from_bytes(data=img_bytes, mime_type=mime))
                except Exception as e:
                    logger.warning(f"Failed to load image {img_path}: {e}")

            # Single batched call with structured output
            response = client.models.generate_content(
                model=self.model,
                contents=[types.Content(role="user", parts=parts)],
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=VLM_RESPONSE_SCHEMA,
                    temperature=0.1,
                ),
            )

            latency_ms = (time.time() - start_time) * 1000

            # Parse response
            raw_text = response.text
            if not raw_text:
                raise VLMError("Empty response from Gemini")

            parsed = json.loads(raw_text)

            # Convert to domain objects
            observed_items = []
            for item_data in parsed.get("observed_items", []):
                observed_items.append(ObservedItem(
                    sku=item_data.get("sku"),
                    name=item_data.get("name", "Unknown"),
                    observed_quantity=item_data.get("observed_quantity", 0),
                    confidence=min(1.0, max(0.0, item_data.get("confidence", 0.0))),
                    observation_text=item_data.get("observation_text", ""),
                ))

            quality = parsed.get("image_quality_assessment", {})
            quality_ok = quality.get("is_sufficient", True)
            quality_issues = quality.get("issues", [])

            return VerificationResult(
                observed_items=observed_items,
                image_quality_ok=quality_ok,
                image_quality_issues=quality_issues,
                overall_notes=parsed.get("overall_notes", ""),
                model_version=self.model,
                latency_ms=latency_ms,
                raw_response=parsed,
            )

        except json.JSONDecodeError as e:
            latency_ms = (time.time() - start_time) * 1000
            raise VLMError(f"Invalid JSON from Gemini: {e}", latency_ms=latency_ms) from e
        except Exception as e:
            latency_ms = (time.time() - start_time) * 1000
            if isinstance(e, VLMError):
                raise
            raise VLMError(f"Gemini API error: {e}", latency_ms=latency_ms) from e


class VerificationResult:
    """Result from the Gemini verification call."""

    def __init__(
        self,
        observed_items: list[ObservedItem],
        image_quality_ok: bool,
        image_quality_issues: list[str],
        overall_notes: str,
        model_version: str,
        latency_ms: float,
        raw_response: dict | None = None,
    ):
        self.observed_items = observed_items
        self.image_quality_ok = image_quality_ok
        self.image_quality_issues = image_quality_issues
        self.overall_notes = overall_notes
        self.model_version = model_version
        self.latency_ms = latency_ms
        self.raw_response = raw_response


class VLMError(Exception):
    """Raised when VLM call fails — triggers fail-open path."""

    def __init__(self, message: str, latency_ms: float = 0.0):
        super().__init__(message)
        self.latency_ms = latency_ms
