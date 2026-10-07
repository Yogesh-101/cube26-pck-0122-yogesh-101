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
import random
import re
import socket
import time
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional

from app.config import get_settings
from app.domain.schemas import (
    CatalogueProduct,
    ObservedItem,
    OrderLine,
    UncertaintyDetail,
    UncertaintyReason,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Transient failure classification (what is worth a second attempt)
# ---------------------------------------------------------------------------

# Upstream is momentarily unable to serve a request that is otherwise valid.
_RETRYABLE_HTTP_CODES = {408, 429, 500, 503, 504}
_RETRYABLE_TOKENS = (
    "unavailable",
    "resource_exhausted",
    "rate limit",
    "ratelimit",
    "too many requests",
    "deadline_exceeded",
    " internal",
    "internal error",
    "overloaded",
    "high demand",
    "timed out",
    "timeout",
    "connection reset",
    "connection aborted",
    "connection refused",
    "remote end closed",
    "temporarily unavailable",
)

# The request will never succeed as sent: credentials, permissions, arguments.
_NON_RETRYABLE_HTTP_CODES = {400, 401, 403, 404, 422}
_NON_RETRYABLE_TOKENS = (
    "invalid_argument",
    "unauthenticated",
    "permission_denied",
    "failed_precondition",
    "api key not valid",
    "api_key_invalid",
    "api key expired",
    "invalid api key",
    "missing api key",
    "unauthorized",
    "not_found",
)

_NETWORK_ERRORS: tuple[type[BaseException], ...] = (
    socket.timeout,
    socket.gaierror,
    TimeoutError,
    ConnectionError,
)


@lru_cache(maxsize=1)
def _genai_error_types() -> tuple[Any, Any]:
    """Resolve the SDK's typed errors, tolerating versions that lack them."""
    try:
        from google.genai import errors as genai_errors

        return (
            getattr(genai_errors, "ServerError", None),
            getattr(genai_errors, "ClientError", None),
        )
    except Exception:
        return (None, None)


def _error_text(exc: BaseException) -> str:
    """Flatten an exception's message and status fields into searchable text."""
    parts = [str(exc)]
    for attr in ("message", "status", "reason"):
        value = getattr(exc, attr, None)
        if isinstance(value, str):
            parts.append(value)
    return " ".join(parts).lower()


def _error_codes(exc: BaseException, text: str) -> set[int]:
    """Collect status codes from typed attributes and from the message text."""
    codes: set[int] = set()
    for attr in ("code", "status_code", "http_status"):
        value = getattr(exc, attr, None)
        if isinstance(value, bool):
            continue
        if isinstance(value, int):
            codes.add(value)
        elif isinstance(value, str) and value.isdigit():
            codes.add(int(value))
    # Generic exceptions carry the status only in their text, e.g.
    # "503 UNAVAILABLE. {'error': {'code': 503, ...}}".
    codes.update(int(m) for m in re.findall(r"\b([45]\d\d)\b", text))
    return codes


def _is_retryable_error(exc: BaseException) -> bool:
    """
    True only for transient upstream failures.

    Non-retryable signals are evaluated first, so an auth, permission or
    argument error can never be retried even when its payload happens to
    mention a retryable word. Parse failures of a successful response are
    never retried either — the model already answered.
    """
    if isinstance(exc, (VLMError, json.JSONDecodeError)):
        return False

    text = _error_text(exc)
    codes = _error_codes(exc, text)

    if codes & _NON_RETRYABLE_HTTP_CODES:
        return False
    if any(token in text for token in _NON_RETRYABLE_TOKENS):
        return False

    server_error, client_error = _genai_error_types()
    if client_error is not None and isinstance(exc, client_error):
        # 4xx from the SDK: only throttling is worth another attempt.
        return bool(codes & {408, 429})
    if server_error is not None and isinstance(exc, server_error):
        return True

    if codes & _RETRYABLE_HTTP_CODES:
        return True
    if any(token in text for token in _RETRYABLE_TOKENS):
        return True

    return isinstance(exc, _NETWORK_ERRORS)


def _suggested_retry_seconds(exc: BaseException) -> float | None:
    """Parse RetryInfo.retryDelay from Gemini error payloads when present."""
    text = str(exc)
    # 'retryDelay': '54s' or "Please retry in 54.04s"
    match = re.search(r"retryDelay['\"]?\s*[:=]\s*['\"]?(\d+(?:\.\d+)?)s?", text, re.I)
    if match:
        return float(match.group(1))
    match = re.search(r"Please retry in (\d+(?:\.\d+)?)s", text, re.I)
    if match:
        return float(match.group(1))
    return None


def _failure_message(exc: BaseException, attempts: int, note: str = "") -> str:
    """Operator-visible reason — this text is persisted on pending records."""
    message = f"Gemini API error: {exc}"
    if attempts > 1:
        message += f" (retried, {attempts} attempts)"
    if note:
        message += f" ({note})"
    return message


# ---------------------------------------------------------------------------
# VLM Response Schema (what we ask Gemini to return)
# ---------------------------------------------------------------------------

VLM_RESPONSE_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "observed_items": {
            "type": "ARRAY",
            "description": "Every distinct product visible in the open package",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "sku": {
                        "type": "STRING",
                        "nullable": True,
                        "description": "Matched SKU from the provided catalogue, or null if no match"
                    },
                    "name": {
                        "type": "STRING",
                        "description": "Product name as identified"
                    },
                    "observed_quantity": {
                        "type": "INTEGER",
                        "description": "Count of this product visible in the package"
                    },
                    "confidence": {
                        "type": "NUMBER",
                        "description": "Confidence in identification (0.0 to 1.0)"
                    },
                    "observation_text": {
                        "type": "STRING",
                        "description": "Brief description of what was observed"
                    }
                },
                "required": ["name", "observed_quantity", "confidence", "observation_text"]
            }
        },
        "image_quality_assessment": {
            "type": "OBJECT",
            "properties": {
                "is_sufficient": {
                    "type": "BOOLEAN",
                    "description": "Whether the images are clear enough for reliable verification"
                },
                "issues": {
                    "type": "ARRAY",
                    "items": {"type": "STRING"},
                    "description": "Any quality issues noticed (blur, glare, occlusion, etc.)"
                }
            },
            "required": ["is_sufficient", "issues"]
        },
        "overall_notes": {
            "type": "STRING",
            "description": "Any additional observations about the package contents"
        }
    },
    "required": ["observed_items", "image_quality_assessment", "overall_notes"]
}


def _build_prompt(
    catalogue: list[CatalogueProduct],
) -> str:
    """
    Build an order-blind observation prompt for the VLM call.

    The expected order is intentionally omitted. Passing expected SKUs/quantities
    into the vision prompt anchors the model toward reporting what should be
    present rather than what is visible. The deterministic decision engine
    compares observations to the order after this call returns.
    """

    catalogue_text = "\n".join(
        f"  - SKU: {p.sku} | Name: {p.name}"
        + (f" | Variant: {p.variant}" if p.variant else "")
        + (f" | Description: {p.description}" if p.description else "")
        for p in catalogue
    ) if catalogue else "  No catalogue provided — identify products by visual appearance only."

    return f"""You are a Pack Manager observation agent. You are examining photographs of an open package before it is sealed for shipping.

TASK: Identify every distinct physical item visible in the open package and count quantities. Match items to the product catalogue when reliable. Do not infer what the order was supposed to contain.

PRODUCT CATALOGUE (for SKU grounding only — not a packing list):
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
9. Do NOT use any prior knowledge of an order. There is no expected packing list in this prompt. Report presence and count only.

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
    Transient upstream failures are retried with exponential backoff.
    Returns parsed ObservedItems or raises on failure for fail-open handling.
    """

    def __init__(
        self,
        api_key: str,
        model: str = "gemini-2.5-flash",
        max_retries: Optional[int] = None,
        retry_base_seconds: Optional[float] = None,
    ):
        settings = get_settings()
        self.api_key = api_key
        self.model = model
        self.max_retries = max(
            0, settings.vlm_max_retries if max_retries is None else int(max_retries)
        )
        self.retry_base_seconds = max(
            0.0,
            float(
                settings.vlm_retry_base_seconds
                if retry_base_seconds is None
                else retry_base_seconds
            ),
        )
        self._client = None

    def _get_client(self):
        """Lazy-init the Gemini client with a hard HTTP timeout."""
        if self._client is None:
            from google import genai
            # Prevent indefinite hangs on overloaded models (esp. gemini-3.8-flash).
            settings = get_settings()
            timeout_ms = max(10_000, int(settings.vlm_timeout_seconds * 1000))
            self._client = genai.Client(
                api_key=self.api_key,
                http_options={"timeout": timeout_ms},
            )
        return self._client

    def _generate_with_retry(
        self,
        client: Any,
        contents: Any,
        config: Any,
        start_time: float,
        timeout_seconds: int,
    ) -> Any:
        """
        Issue the batched request, retrying only transient upstream failures.

        Every attempt sends the same single request carrying all checks, and a
        success returns immediately, so the one-call-per-unit rule holds. The
        retry budget is bounded by both max_retries and timeout_seconds.
        """
        budget_seconds = max(0.0, float(timeout_seconds))
        total_attempts = self.max_retries + 1
        attempts = 0

        while True:
            attempts += 1
            try:
                return client.models.generate_content(
                    model=self.model,
                    contents=contents,
                    config=config,
                )
            except Exception as e:
                elapsed = time.time() - start_time

                if attempts > self.max_retries or not _is_retryable_error(e):
                    raise VLMError(
                        _failure_message(e, attempts),
                        latency_ms=elapsed * 1000,
                    ) from e

                backoff = self.retry_base_seconds * (2 ** (attempts - 1))
                backoff += random.uniform(0.0, self.retry_base_seconds * 0.25)
                suggested = _suggested_retry_seconds(e)
                if suggested is not None:
                    # Daily free-tier exhaustion returns multi-hour delays.
                    # Do not burn the per-call budget spinning on that.
                    if suggested > budget_seconds:
                        raise VLMError(
                            _failure_message(
                                e, attempts,
                                note="upstream retry delay exceeds call budget (likely daily quota)",
                            ),
                            latency_ms=elapsed * 1000,
                        ) from e
                    backoff = max(backoff, min(suggested, 90.0))

                if backoff >= budget_seconds - elapsed:
                    raise VLMError(
                        _failure_message(
                            e, attempts,
                            note=f"{timeout_seconds}s time budget exhausted",
                        ),
                        latency_ms=elapsed * 1000,
                    ) from e

                logger.warning(
                    f"Transient Gemini failure on attempt {attempts}/{total_attempts}, "
                    f"retrying in {backoff:.2f}s: {e}"
                )
                time.sleep(backoff)

    def verify_package(
        self,
        image_paths: list[str],
        catalogue: list[CatalogueProduct],
        order_lines: list[OrderLine] | None = None,
        timeout_seconds: int = 60,
    ) -> VerificationResult:
        """
        Single batched VLM call for pack observation (order-blind).

        Args:
            image_paths: Paths to package photographs.
            catalogue: Product catalogue for SKU grounding only.
            order_lines: Unused by the prompt. Accepted for backward
                compatibility with earlier call sites; never sent to the model.
            timeout_seconds: Max wait time for the model response, and the
                total budget shared by any retries of a transient failure.

        Returns:
            VerificationResult with observed items, quality info, and metadata.

        Raises:
            VLMError: On any failure (caller implements fail-open).
        """
        # order_lines is intentionally unused: observations stay order-blind.
        _ = order_lines
        start_time = time.time()

        try:
            client = self._get_client()

            # Build order-blind prompt (catalogue only — no expected order)
            prompt_text = _build_prompt(catalogue)

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
                        ".gif": "image/gif",
                    }
                    mime = mime_map.get(suffix, "image/jpeg")
                    parts.append(types.Part.from_bytes(data=img_bytes, mime_type=mime))
                except Exception as e:
                    logger.warning(f"Failed to load image {img_path}: {e}")

            # Single batched call with structured output, retried on transient
            # upstream failures only (the payload is built once, above).
            response = self._generate_with_retry(
                client=client,
                contents=[types.Content(role="user", parts=parts)],
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=VLM_RESPONSE_SCHEMA,
                    temperature=0.1,
                ),
                start_time=start_time,
                timeout_seconds=timeout_seconds,
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
