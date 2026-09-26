"""
Tests for the image quality gate.

Verifies that unusable images are rejected before wasting model calls.
"""

import os
import tempfile

import pytest

from app.vision.quality import assess_image_quality, assess_batch_quality, compute_file_sha256


@pytest.fixture
def tmp_image():
    """Create a simple valid test image using Pillow."""
    from PIL import Image

    fd, path = tempfile.mkstemp(suffix=".jpg")
    os.close(fd)
    img = Image.new("RGB", (640, 480), color=(128, 128, 128))
    img.save(path, "JPEG")
    yield path
    if os.path.exists(path):
        os.unlink(path)


@pytest.fixture
def dark_image():
    """Create a very dark image."""
    from PIL import Image

    fd, path = tempfile.mkstemp(suffix=".jpg")
    os.close(fd)
    img = Image.new("RGB", (640, 480), color=(10, 10, 10))
    img.save(path, "JPEG")
    yield path
    if os.path.exists(path):
        os.unlink(path)


@pytest.fixture
def bright_image():
    """Create an overexposed image."""
    from PIL import Image

    fd, path = tempfile.mkstemp(suffix=".jpg")
    os.close(fd)
    img = Image.new("RGB", (640, 480), color=(252, 252, 252))
    img.save(path, "JPEG")
    yield path
    if os.path.exists(path):
        os.unlink(path)


@pytest.fixture
def tiny_image():
    """Create a tiny (resolution too low) image."""
    from PIL import Image

    fd, path = tempfile.mkstemp(suffix=".jpg")
    os.close(fd)
    img = Image.new("RGB", (50, 50), color=(128, 128, 128))
    img.save(path, "JPEG")
    yield path
    if os.path.exists(path):
        os.unlink(path)


class TestImageQuality:
    def test_valid_image(self, tmp_image):
        result = assess_image_quality(tmp_image)
        assert result.is_usable is True
        assert result.sha256 != ""
        assert len(result.sha256) == 64

    def test_missing_file(self):
        result = assess_image_quality("/nonexistent/path.jpg")
        assert result.is_usable is False
        assert any("not found" in i.lower() for i in result.issues)

    def test_dark_image_flagged(self, dark_image):
        result = assess_image_quality(dark_image)
        assert result.is_uncertain is True
        assert any("dark" in i.lower() for i in result.issues)

    def test_bright_image_flagged(self, bright_image):
        result = assess_image_quality(bright_image)
        assert result.is_uncertain is True
        assert any("overexposed" in i.lower() or "bright" in i.lower() for i in result.issues)

    def test_tiny_image_rejected(self, tiny_image):
        result = assess_image_quality(tiny_image)
        assert result.is_usable is False

    def test_sha256_deterministic(self, tmp_image):
        h1 = compute_file_sha256(tmp_image)
        h2 = compute_file_sha256(tmp_image)
        assert h1 == h2
        assert len(h1) == 64


class TestBatchQuality:
    def test_batch_all_valid(self, tmp_image):
        results, all_ok, any_uncertain = assess_batch_quality([tmp_image])
        assert all_ok is True
        assert any_uncertain is False
        assert len(results) == 1

    def test_batch_detects_duplicates(self, tmp_image):
        results, all_ok, any_uncertain = assess_batch_quality([tmp_image, tmp_image])
        assert any_uncertain is True
        assert any("Duplicate" in i for r in results for i in r.issues)

    def test_batch_with_bad_image(self, tmp_image):
        results, all_ok, any_uncertain = assess_batch_quality([tmp_image, "/nonexistent.jpg"])
        assert all_ok is False
