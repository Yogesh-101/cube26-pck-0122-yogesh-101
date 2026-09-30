"""
Tests for the VLM transient-failure retry policy.

A momentary Gemini 503 must not throw away an inspection, while auth and
argument errors must still fail on the first attempt. No live API calls:
the SDK client is stubbed and backoff is zeroed out.
"""

import json

import pytest

from app.config import get_settings
from app.domain.schemas import CatalogueProduct, OrderLine
from app.vision.gemini_client import (
    GeminiPackVerifier,
    VLMError,
    VerificationResult,
    _is_retryable_error,
)

GOOD_RESPONSE = {
    "observed_items": [
        {
            "sku": "SKU-BOTTLE-750",
            "name": "Water Bottle 750ml",
            "observed_quantity": 1,
            "confidence": 0.9,
            "observation_text": "Blue steel bottle, label visible",
        }
    ],
    "image_quality_assessment": {"is_sufficient": True, "issues": []},
    "overall_notes": "Single item, clearly visible",
}

# Text of the real outage that produced a useless PENDING record.
ERROR_503_TEXT = (
    "503 UNAVAILABLE. {'error': {'code': 503, 'message': 'This model is currently "
    "experiencing high demand. Spikes in demand are usually temporary. Please try "
    "again later.', 'status': 'UNAVAILABLE'}}"
)
ERROR_400_TEXT = (
    "400 INVALID_ARGUMENT. {'error': {'code': 400, 'message': 'API key not valid. "
    "Please pass a valid API key.', 'status': 'INVALID_ARGUMENT'}}"
)
ERROR_403_TEXT = (
    "403 PERMISSION_DENIED. {'error': {'code': 403, 'message': 'Requests to this API "
    "are blocked.', 'status': 'PERMISSION_DENIED'}}"
)


class FakeResponse:
    def __init__(self, text: str):
        self.text = text


class FakeModels:
    """Stands in for client.models, recording every attempt."""

    def __init__(self, side_effects):
        self.side_effects = list(side_effects)
        self.attempts = 0
        self.calls = []

    def generate_content(self, model, contents, config):
        self.attempts += 1
        self.calls.append({"model": model, "contents": contents, "config": config})
        effect = self.side_effects[min(self.attempts - 1, len(self.side_effects) - 1)]
        if isinstance(effect, BaseException):
            raise effect
        return effect


class FakeClient:
    def __init__(self, side_effects):
        self.models = FakeModels(side_effects)


class TypedApiError(Exception):
    """Mimics google.genai APIError, which exposes code/status attributes."""

    def __init__(self, code: int, status: str, message: str):
        super().__init__(f"{code} {status}. {message}")
        self.code = code
        self.status = status
        self.message = message


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    """Keep the suite fast — never actually wait out a backoff."""
    monkeypatch.setattr("time.sleep", lambda seconds: None)


@pytest.fixture
def image_path(tmp_path):
    path = tmp_path / "package.jpg"
    path.write_bytes(b"\xff\xd8\xff\xdb" + b"\x00" * 128 + b"\xff\xd9")
    return str(path)


def make_verifier(side_effects, max_retries=2):
    verifier = GeminiPackVerifier(
        api_key="test-key-not-used",
        model="gemini-2.5-flash",
        max_retries=max_retries,
        retry_base_seconds=0.0,
    )
    client = FakeClient(side_effects)
    verifier._client = client  # bypass lazy SDK init — no network, no real key
    return verifier, client


def run(verifier, image_path, timeout_seconds=60):
    return verifier.verify_package(
        image_paths=[image_path],
        order_lines=[OrderLine(sku="SKU-BOTTLE-750", quantity=1)],
        catalogue=[CatalogueProduct(sku="SKU-BOTTLE-750", name="Water Bottle 750ml")],
        timeout_seconds=timeout_seconds,
    )


class TestTransientRetry:
    def test_503_then_success(self, image_path):
        verifier, client = make_verifier(
            [Exception(ERROR_503_TEXT), FakeResponse(json.dumps(GOOD_RESPONSE))]
        )

        result = run(verifier, image_path)

        assert isinstance(result, VerificationResult)
        assert client.models.attempts == 2
        assert len(result.observed_items) == 1
        assert result.observed_items[0].sku == "SKU-BOTTLE-750"
        assert result.image_quality_ok is True
        assert result.model_version == "gemini-2.5-flash"

    def test_no_extra_call_after_success(self, image_path):
        verifier, client = make_verifier([FakeResponse(json.dumps(GOOD_RESPONSE))])

        run(verifier, image_path)

        # One batched call per unit: the success is not followed by anything.
        assert client.models.attempts == 1

    def test_typed_429_then_success(self, image_path):
        verifier, client = make_verifier(
            [
                TypedApiError(429, "RESOURCE_EXHAUSTED", "Quota exceeded, retry later"),
                FakeResponse(json.dumps(GOOD_RESPONSE)),
            ]
        )

        result = run(verifier, image_path)

        assert isinstance(result, VerificationResult)
        assert client.models.attempts == 2

    def test_network_timeout_then_success(self, image_path):
        verifier, client = make_verifier(
            [TimeoutError("The read operation timed out"), FakeResponse(json.dumps(GOOD_RESPONSE))]
        )

        result = run(verifier, image_path)

        assert isinstance(result, VerificationResult)
        assert client.models.attempts == 2


class TestRetryBudget:
    def test_persistent_503_respects_budget(self, image_path):
        verifier, client = make_verifier([Exception(ERROR_503_TEXT)], max_retries=2)

        with pytest.raises(VLMError) as exc_info:
            run(verifier, image_path)

        assert client.models.attempts == 3  # 1 initial + 2 retries, not infinite
        message = str(exc_info.value)
        assert "503" in message and "UNAVAILABLE" in message
        assert "3 attempts" in message

    def test_zero_retries_configured(self, image_path):
        verifier, client = make_verifier([Exception(ERROR_503_TEXT)], max_retries=0)

        with pytest.raises(VLMError):
            run(verifier, image_path)

        assert client.models.attempts == 1

    def test_time_budget_stops_retrying(self, image_path):
        verifier = GeminiPackVerifier(
            api_key="test-key-not-used",
            max_retries=5,
            retry_base_seconds=30.0,
        )
        client = FakeClient([Exception(ERROR_503_TEXT)])
        verifier._client = client

        with pytest.raises(VLMError) as exc_info:
            run(verifier, image_path, timeout_seconds=1)

        assert client.models.attempts == 1  # no room in the budget for a 30s wait
        assert "time budget" in str(exc_info.value)


class TestNonRetryableFailures:
    def test_invalid_argument_not_retried(self, image_path):
        verifier, client = make_verifier([Exception(ERROR_400_TEXT)])

        with pytest.raises(VLMError) as exc_info:
            run(verifier, image_path)

        assert client.models.attempts == 1
        assert "retried" not in str(exc_info.value)

    def test_permission_denied_not_retried(self, image_path):
        verifier, client = make_verifier(
            [TypedApiError(403, "PERMISSION_DENIED", "Requests to this API are blocked.")]
        )

        with pytest.raises(VLMError):
            run(verifier, image_path)

        assert client.models.attempts == 1

    def test_missing_api_key_not_retried(self, image_path):
        verifier, client = make_verifier([ValueError("Missing API key. Please set GEMINI_API_KEY.")])

        with pytest.raises(VLMError):
            run(verifier, image_path)

        assert client.models.attempts == 1

    def test_bad_json_not_retried(self, image_path):
        verifier, client = make_verifier([FakeResponse("this is not json")])

        with pytest.raises(VLMError) as exc_info:
            run(verifier, image_path)

        assert client.models.attempts == 1
        assert "Invalid JSON" in str(exc_info.value)

    def test_empty_response_not_retried(self, image_path):
        verifier, client = make_verifier([FakeResponse("")])

        with pytest.raises(VLMError):
            run(verifier, image_path)

        assert client.models.attempts == 1


class TestErrorClassification:
    @pytest.mark.parametrize(
        "error",
        [
            Exception(ERROR_503_TEXT),
            Exception("429 RESOURCE_EXHAUSTED. Rate limit exceeded"),
            Exception("500 INTERNAL. An internal error has occurred"),
            Exception("504 DEADLINE_EXCEEDED. The operation timed out"),
            TimeoutError("timed out"),
            ConnectionError("Connection reset by peer"),
            TypedApiError(503, "UNAVAILABLE", "Model overloaded"),
        ],
    )
    def test_retryable(self, error):
        assert _is_retryable_error(error) is True

    @pytest.mark.parametrize(
        "error",
        [
            Exception(ERROR_400_TEXT),
            Exception(ERROR_403_TEXT),
            Exception("401 UNAUTHENTICATED. API key not valid"),
            TypedApiError(400, "INVALID_ARGUMENT", "Unsupported mime type"),
            TypedApiError(401, "UNAUTHENTICATED", "Invalid API key"),
            ValueError("Missing API key"),
            VLMError("Empty response from Gemini"),
        ],
    )
    def test_not_retryable(self, error):
        assert _is_retryable_error(error) is False

    def test_json_decode_error_not_retryable(self):
        try:
            json.loads("{oops")
        except json.JSONDecodeError as e:
            assert _is_retryable_error(e) is False


class TestRetrySettings:
    def test_defaults_are_bounded(self):
        settings = get_settings()
        assert settings.vlm_max_retries >= 0
        assert settings.vlm_retry_base_seconds > 0

    def test_verifier_falls_back_to_settings(self):
        settings = get_settings()
        verifier = GeminiPackVerifier(api_key="test-key-not-used")
        assert verifier.max_retries == settings.vlm_max_retries
        assert verifier.retry_base_seconds == settings.vlm_retry_base_seconds
