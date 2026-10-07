"""
Tests for the OpenCV image quality gate.

Covers uploads, low light, blur, and held-out ambiguous fixtures.
"""

import os
import tempfile

import pytest

from app.vision.quality import (
    ENGINE_OPENCV,
    assess_batch_quality,
    assess_image_bytes,
    assess_image_quality,
    compute_file_sha256,
    _opencv_available,
)


@pytest.fixture
def tmp_image():
    """Sharp, well-lit checkerboard — should pass the OpenCV gate."""
    from PIL import Image, ImageDraw

    fd, path = tempfile.mkstemp(suffix=".jpg")
    os.close(fd)
    img = Image.new("RGB", (640, 480), color=(160, 160, 160))
    draw = ImageDraw.Draw(img)
    for y in range(0, 480, 40):
        for x in range(0, 640, 40):
            if ((x // 40) + (y // 40)) % 2 == 0:
                draw.rectangle([x, y, x + 39, y + 39], fill=(200, 200, 200))
    img.save(path, "JPEG", quality=95)
    yield path
    if os.path.exists(path):
        os.unlink(path)


@pytest.fixture
def dark_image():
    """Very dark but with slight texture so decode stays valid."""
    from PIL import Image, ImageDraw

    fd, path = tempfile.mkstemp(suffix=".jpg")
    os.close(fd)
    img = Image.new("RGB", (640, 480), color=(12, 12, 12))
    draw = ImageDraw.Draw(img)
    for y in range(0, 480, 80):
        draw.line([(0, y), (640, y)], fill=(28, 28, 28), width=2)
    img.save(path, "JPEG", quality=90)
    yield path
    if os.path.exists(path):
        os.unlink(path)


@pytest.fixture
def bright_image():
    """Nearly overexposed frame."""
    from PIL import Image, ImageDraw

    fd, path = tempfile.mkstemp(suffix=".jpg")
    os.close(fd)
    img = Image.new("RGB", (640, 480), color=(250, 250, 250))
    draw = ImageDraw.Draw(img)
    for y in range(0, 480, 80):
        draw.line([(0, y), (640, y)], fill=(255, 255, 255), width=2)
    img.save(path, "JPEG", quality=90)
    yield path
    if os.path.exists(path):
        os.unlink(path)


@pytest.fixture
def tiny_image():
    from PIL import Image

    fd, path = tempfile.mkstemp(suffix=".jpg")
    os.close(fd)
    img = Image.new("RGB", (50, 50), color=(128, 128, 128))
    img.save(path, "JPEG")
    yield path
    if os.path.exists(path):
        os.unlink(path)


@pytest.fixture
def blurry_image():
    """Heavily blurred pack-like frame — Laplacian should be low."""
    from PIL import Image, ImageDraw, ImageFilter

    fd, path = tempfile.mkstemp(suffix=".jpg")
    os.close(fd)
    img = Image.new("RGB", (640, 480), color=(140, 140, 140))
    draw = ImageDraw.Draw(img)
    draw.rectangle([80, 80, 560, 400], fill=(90, 90, 90))
    draw.ellipse([200, 160, 440, 320], fill=(180, 180, 180))
    img = img.filter(ImageFilter.GaussianBlur(radius=12))
    img.save(path, "JPEG", quality=85)
    yield path
    if os.path.exists(path):
        os.unlink(path)


class TestImageQuality:
    def test_valid_image(self, tmp_image):
        result = assess_image_quality(tmp_image)
        assert result.is_usable is True
        assert result.is_uncertain is False
        assert result.sha256 != ""
        assert len(result.sha256) == 64
        if _opencv_available():
            assert result.engine == ENGINE_OPENCV

    def test_missing_file(self):
        result = assess_image_quality("/nonexistent/path.jpg")
        assert result.is_usable is False
        assert any("not found" in i.lower() for i in result.issues)

    def test_dark_image_flagged(self, dark_image):
        result = assess_image_quality(dark_image)
        assert result.is_uncertain is True or result.is_usable is False
        assert any("dark" in i.lower() or "light" in i.lower() or "underexpose" in i.lower() for i in result.issues)

    def test_bright_image_flagged(self, bright_image):
        result = assess_image_quality(bright_image)
        assert result.is_uncertain is True or result.is_usable is False
        assert any("overexpose" in i.lower() or "bright" in i.lower() for i in result.issues)

    def test_blurry_image_flagged(self, blurry_image):
        result = assess_image_quality(blurry_image)
        assert result.is_uncertain is True
        assert any("blur" in i.lower() for i in result.issues)

    def test_tiny_image_rejected(self, tiny_image):
        result = assess_image_quality(tiny_image)
        assert result.is_usable is False

    def test_bytes_upload_path(self, tmp_image):
        data = open(tmp_image, "rb").read()
        result = assess_image_bytes(data, label="upload.jpg", source="upload")
        assert result.is_usable is True
        assert result.source == "upload"
        assert result.sha256 == compute_file_sha256(tmp_image)

    def test_sha256_deterministic(self, tmp_image):
        h1 = compute_file_sha256(tmp_image)
        h2 = compute_file_sha256(tmp_image)
        assert h1 == h2
        assert len(h1) == 64

    def test_to_dict_contract(self, tmp_image):
        d = assess_image_quality(tmp_image).to_dict()
        assert "blur_score" in d and "contrast" in d and "ok_to_seal_path" in d


class TestHeldOutAmbiguousFixtures:
    def test_ambig_fixtures_flagged_uncertain(self):
        from pathlib import Path

        root = Path(__file__).resolve().parents[2] / "data" / "eval" / "held_out" / "images"
        if not root.exists():
            pytest.skip("held-out fixtures not present")
        for name in ("HO-AMBIG-01.jpg", "HO-AMBIG-02.jpg", "HO-AMBIG-03.jpg", "HO-AMBIG-04.jpg"):
            result = assess_image_quality(str(root / name))
            assert result.is_usable is True
            assert result.is_uncertain is True, f"{name} should be uncertain: {result.issues}"
            if _opencv_available():
                assert result.engine == ENGINE_OPENCV


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
