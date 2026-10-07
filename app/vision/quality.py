"""
PCK Pack Manager — Image Quality Gate

Deterministic checks on package photographs before sending to VLM.
Rejects or flags images that are too blurry, too dark/bright, duplicates,
or too large. This avoids wasting model calls on unusable images.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

# Thresholds (configurable via settings in production)
# OpenCV Laplacian variance and Pillow kernel variance use different scales.
BLUR_THRESHOLD_OPENCV = 100.0
BLUR_THRESHOLD_PILLOW = 40.0   # AMBIG fixtures ~10–26; sharp pack photos typically >60
BRIGHTNESS_MIN = 85.0         # Mean pixel value below this = too dark / underlit
BRIGHTNESS_MAX = 250.0        # Mean pixel value above this = overexposed
MAX_FILE_SIZE_MB = 20


def _laplacian_variance_pillow(gray) -> float:
    """OpenCV-free blur proxy: variance of a 3x3 Laplacian kernel response."""
    from PIL import ImageFilter

    # Discrete Laplacian kernel (same shape as cv2.Laplacian default)
    kernel = ImageFilter.Kernel(
        size=(3, 3),
        kernel=[0, 1, 0, 1, -4, 1, 0, 1, 0],
        scale=1,
        offset=128,
    )
    response = gray.filter(kernel)
    try:
        pixels = list(response.get_flattened_data())
    except AttributeError:
        pixels = list(response.getdata())
    if not pixels:
        return 0.0
    mean = sum(pixels) / len(pixels)
    return sum((p - mean) ** 2 for p in pixels) / len(pixels)


@dataclass
class QualityResult:
    """Result of image quality assessment."""
    path: str
    is_usable: bool = True
    is_uncertain: bool = False
    blur_score: float = 0.0
    brightness: float = 128.0
    file_size_mb: float = 0.0
    sha256: str = ""
    issues: list[str] = field(default_factory=list)


def compute_file_sha256(path: str) -> str:
    """Compute SHA-256 hash of a file for deduplication and evidence."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def assess_image_quality(path: str) -> QualityResult:
    """
    Assess a single image's quality using deterministic CV checks.

    Uses Pillow for basic checks, with optional OpenCV for Laplacian blur detection.
    Gracefully degrades if OpenCV is not available.
    """
    result = QualityResult(path=path)

    # File existence and size
    file_path = Path(path)
    if not file_path.exists():
        result.is_usable = False
        result.issues.append(f"File not found: {path}")
        return result

    file_size = file_path.stat().st_size / (1024 * 1024)
    result.file_size_mb = round(file_size, 2)

    if file_size > MAX_FILE_SIZE_MB:
        result.is_usable = False
        result.issues.append(f"File too large: {file_size:.1f}MB (max {MAX_FILE_SIZE_MB}MB)")
        return result

    if file_size < 0.001:
        result.is_usable = False
        result.issues.append("File is empty or near-empty")
        return result

    # SHA-256 for deduplication
    result.sha256 = compute_file_sha256(path)

    # Image checks via Pillow
    try:
        from PIL import Image
        img = Image.open(path)
        img.verify()
        img = Image.open(path)  # reopen after verify

        # Check minimum resolution
        w, h = img.size
        if w < 100 or h < 100:
            result.is_usable = False
            result.issues.append(f"Resolution too low: {w}x{h}")
            return result

        # Brightness check (convert to grayscale, compute mean)
        gray = img.convert("L")
        pixels = list(gray.tobytes())
        mean_brightness = sum(pixels) / len(pixels) if len(pixels) > 0 else 128.0
        result.brightness = round(mean_brightness, 1)

        if mean_brightness < BRIGHTNESS_MIN:
            result.is_uncertain = True
            result.issues.append(f"Image may be too dark (brightness={mean_brightness:.0f})")
        elif mean_brightness > BRIGHTNESS_MAX:
            result.is_uncertain = True
            result.issues.append(f"Image may be overexposed (brightness={mean_brightness:.0f})")

    except Exception as e:
        result.is_usable = False
        result.issues.append(f"Cannot open image: {e}")
        return result

    # Blur detection: prefer OpenCV Laplacian; fall back to Pillow so we never skip
    blur_threshold = BLUR_THRESHOLD_PILLOW
    try:
        import cv2

        cv_img = cv2.imread(path, cv2.IMREAD_GRAYSCALE)
        if cv_img is not None:
            laplacian_var = float(cv2.Laplacian(cv_img, cv2.CV_64F).var())
            result.blur_score = round(laplacian_var, 2)
            blur_threshold = BLUR_THRESHOLD_OPENCV
        else:
            laplacian_var = _laplacian_variance_pillow(gray)
            result.blur_score = round(laplacian_var, 2)
    except ImportError:
        laplacian_var = _laplacian_variance_pillow(gray)
        result.blur_score = round(laplacian_var, 2)
    except Exception as e:
        logger.debug("Blur detection failed (%s); using Pillow fallback", e)
        laplacian_var = _laplacian_variance_pillow(gray)
        result.blur_score = round(laplacian_var, 2)

    if result.blur_score >= 0 and result.blur_score < blur_threshold:
        result.is_uncertain = True
        result.issues.append(f"Image may be blurry (score={result.blur_score:.0f})")

    return result


def assess_batch_quality(paths: list[str]) -> tuple[list[QualityResult], bool, bool]:
    """
    Assess quality for a batch of images.

    Returns:
        (results, all_ok, any_uncertain)
        - results: per-image quality results
        - all_ok: True if all images are usable
        - any_uncertain: True if any image has uncertain quality
    """
    results = [assess_image_quality(p) for p in paths]

    # Detect duplicates by SHA-256
    seen_hashes: dict[str, str] = {}
    for r in results:
        if r.sha256:
            if r.sha256 in seen_hashes:
                r.is_uncertain = True
                r.issues.append(f"Duplicate of {seen_hashes[r.sha256]}")
            else:
                seen_hashes[r.sha256] = r.path

    all_ok = all(r.is_usable and not r.is_uncertain for r in results)
    any_uncertain = any(r.is_uncertain for r in results)
    any_unusable = any(not r.is_usable for r in results)

    return results, not any_unusable and not any_uncertain, any_uncertain
