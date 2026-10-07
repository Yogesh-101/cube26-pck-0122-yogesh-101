"""
PCK Pack Manager — Image Quality Gate (OpenCV-first)

Deterministic checks on package photographs before sending to VLM.
Handles uploads, live camera frames, and low-light / blur / contrast failures
so ambiguous evidence never auto-seals.

OpenCV is the primary engine (`opencv-python-headless`). Pillow remains a
fallback so the gate still runs if OpenCV cannot decode a file.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import BinaryIO

logger = logging.getLogger(__name__)

# --- Thresholds (OpenCV scale; calibrated on held-out pack fixtures) ---
BLUR_THRESHOLD_OPENCV = 50.0      # Laplacian variance; AMBIG ~0.5–1, sharp packs typically >70
BLUR_THRESHOLD_PILLOW = 40.0
BRIGHTNESS_MIN = 85.0             # mean luminance 0–255
BRIGHTNESS_MAX = 245.0
CONTRAST_MIN = 18.0               # grayscale stddev; low = flat / foggy / underexposed
UNDEREXPOSE_PCT_MAX = 35.0        # % pixels below dark floor
OVEREXPOSE_PCT_MAX = 35.0         # % pixels above highlight ceiling
DARK_FLOOR = 40
HIGHLIGHT_CEILING = 220
MIN_WIDTH = 200
MIN_HEIGHT = 200
MAX_FILE_SIZE_MB = 20
ENGINE_OPENCV = "opencv"
ENGINE_PILLOW = "pillow"


def _laplacian_variance_pillow(gray) -> float:
    """OpenCV-free blur proxy when cv2 cannot be used."""
    from PIL import ImageFilter

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
    contrast: float = 0.0
    underexpose_pct: float = 0.0
    overexpose_pct: float = 0.0
    width: int = 0
    height: int = 0
    file_size_mb: float = 0.0
    sha256: str = ""
    engine: str = ENGINE_PILLOW
    source: str = "file"  # file | bytes | array | live
    issues: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "path": self.path,
            "is_usable": self.is_usable,
            "is_uncertain": self.is_uncertain,
            "blur_score": self.blur_score,
            "brightness": self.brightness,
            "contrast": self.contrast,
            "underexpose_pct": self.underexpose_pct,
            "overexpose_pct": self.overexpose_pct,
            "width": self.width,
            "height": self.height,
            "file_size_mb": self.file_size_mb,
            "sha256": self.sha256,
            "engine": self.engine,
            "source": self.source,
            "issues": list(self.issues),
            "ok_to_seal_path": self.is_usable and not self.is_uncertain,
        }


def compute_file_sha256(path: str) -> str:
    """Compute SHA-256 hash of a file for deduplication and evidence."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def compute_bytes_sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _opencv_available() -> bool:
    try:
        import cv2  # noqa: F401
        import numpy  # noqa: F401

        return True
    except ImportError:
        return False


def _decode_bgr_from_path(path: str):
    import cv2
    import numpy as np

    # IMREAD_UNCHANGED then convert — handles phone photos better than grayscale-only
    data = np.fromfile(path, dtype=np.uint8)
    if data.size == 0:
        return None
    img = cv2.imdecode(data, cv2.IMREAD_COLOR)
    return img


def _decode_bgr_from_bytes(data: bytes):
    import cv2
    import numpy as np

    arr = np.frombuffer(data, dtype=np.uint8)
    if arr.size == 0:
        return None
    return cv2.imdecode(arr, cv2.IMREAD_COLOR)


def _apply_exif_orientation(bgr):
    """No-op if EXIF missing; OpenCV imdecode drops EXIF — Pillow path handles rotate for files."""
    return bgr


def _analyse_bgr(bgr, result: QualityResult) -> QualityResult:
    """Core OpenCV metrics on a BGR uint8 image."""
    import cv2
    import numpy as np

    if bgr is None or getattr(bgr, "size", 0) == 0:
        result.is_usable = False
        result.issues.append("OpenCV could not decode image bytes")
        return result

    h, w = bgr.shape[:2]
    result.width, result.height = int(w), int(h)
    if w < MIN_WIDTH or h < MIN_HEIGHT:
        result.is_usable = False
        result.issues.append(f"Resolution too low: {w}x{h} (min {MIN_WIDTH}x{MIN_HEIGHT})")
        return result

    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)

    # Blur — Laplacian variance (works for uploads and live frames)
    blur = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    result.blur_score = round(blur, 2)
    if blur < BLUR_THRESHOLD_OPENCV:
        result.is_uncertain = True
        result.issues.append(f"Image may be blurry / out of focus (score={blur:.0f})")

    # Lighting — mean + clipped histogram tails (low light & harsh flash)
    brightness = float(np.mean(gray))
    result.brightness = round(brightness, 1)
    under = float(np.mean(gray < DARK_FLOOR) * 100.0)
    over = float(np.mean(gray > HIGHLIGHT_CEILING) * 100.0)
    result.underexpose_pct = round(under, 1)
    result.overexpose_pct = round(over, 1)

    if brightness < BRIGHTNESS_MIN:
        result.is_uncertain = True
        result.issues.append(f"Image may be too dark / low light (brightness={brightness:.0f})")
    elif brightness > BRIGHTNESS_MAX:
        result.is_uncertain = True
        result.issues.append(f"Image may be overexposed (brightness={brightness:.0f})")

    if under > UNDEREXPOSE_PCT_MAX:
        result.is_uncertain = True
        result.issues.append(f"Large underexposed region ({under:.0f}% of pixels)")
    if over > OVEREXPOSE_PCT_MAX:
        result.is_uncertain = True
        result.issues.append(f"Large overexposed / blown-out region ({over:.0f}% of pixels)")

    # Contrast — flat frames (fog, heavy noise reduction, empty wall) are unreliable
    contrast = float(np.std(gray))
    result.contrast = round(contrast, 1)
    if contrast < CONTRAST_MIN:
        result.is_uncertain = True
        result.issues.append(f"Low contrast (std={contrast:.0f}) — lighting or focus may be poor")

    # Near-uniform / solid colour (often a failed live capture shutter)
    if contrast < 5.0:
        result.is_usable = False
        result.issues.append("Image looks empty or solid-colour — retake the open-box photo")

    result.engine = ENGINE_OPENCV
    return result


def _analyse_with_pillow(path: str | None, data: bytes | None, result: QualityResult) -> QualityResult:
    from PIL import Image, ImageOps
    import io

    try:
        if data is not None:
            img = Image.open(io.BytesIO(data))
        else:
            img = Image.open(path)  # type: ignore[arg-type]
        img = ImageOps.exif_transpose(img)
        img = img.convert("RGB")
    except Exception as e:
        result.is_usable = False
        result.issues.append(f"Cannot open image: {e}")
        return result

    w, h = img.size
    result.width, result.height = int(w), int(h)
    if w < MIN_WIDTH or h < MIN_HEIGHT:
        result.is_usable = False
        result.issues.append(f"Resolution too low: {w}x{h} (min {MIN_WIDTH}x{MIN_HEIGHT})")
        return result

    gray = img.convert("L")
    pixels = list(gray.tobytes())
    mean_brightness = sum(pixels) / len(pixels) if pixels else 128.0
    result.brightness = round(mean_brightness, 1)
    if pixels:
        mean = mean_brightness
        result.contrast = round((sum((p - mean) ** 2 for p in pixels) / len(pixels)) ** 0.5, 1)
        result.underexpose_pct = round(100.0 * sum(1 for p in pixels if p < DARK_FLOOR) / len(pixels), 1)
        result.overexpose_pct = round(100.0 * sum(1 for p in pixels if p > HIGHLIGHT_CEILING) / len(pixels), 1)

    if mean_brightness < BRIGHTNESS_MIN:
        result.is_uncertain = True
        result.issues.append(f"Image may be too dark / low light (brightness={mean_brightness:.0f})")
    elif mean_brightness > BRIGHTNESS_MAX:
        result.is_uncertain = True
        result.issues.append(f"Image may be overexposed (brightness={mean_brightness:.0f})")

    if result.contrast < CONTRAST_MIN:
        result.is_uncertain = True
        result.issues.append(f"Low contrast (std={result.contrast:.0f})")

    blur = _laplacian_variance_pillow(gray)
    result.blur_score = round(blur, 2)
    if blur < BLUR_THRESHOLD_PILLOW:
        result.is_uncertain = True
        result.issues.append(f"Image may be blurry / out of focus (score={blur:.0f})")

    result.engine = ENGINE_PILLOW
    return result


def assess_image_array(bgr, *, label: str = "live-frame", source: str = "live") -> QualityResult:
    """
    Assess a live camera / numpy BGR frame without writing to disk.

    `bgr` must be an HxWx3 uint8 array (OpenCV convention).
    """
    result = QualityResult(path=label, source=source)
    if not _opencv_available():
        result.is_usable = False
        result.issues.append("OpenCV is required for live frame assessment")
        return result
    return _analyse_bgr(_apply_exif_orientation(bgr), result)


def assess_image_bytes(
    data: bytes,
    *,
    label: str = "upload",
    source: str = "bytes",
    max_mb: float = MAX_FILE_SIZE_MB,
) -> QualityResult:
    """Assess raw image bytes from an upload or webcam snapshot (JPEG/PNG/WebP)."""
    result = QualityResult(path=label, source=source)
    size_mb = len(data) / (1024 * 1024)
    result.file_size_mb = round(size_mb, 2)
    if size_mb > max_mb:
        result.is_usable = False
        result.issues.append(f"File too large: {size_mb:.1f}MB (max {max_mb}MB)")
        return result
    if len(data) < 32:
        result.is_usable = False
        result.issues.append("File is empty or near-empty")
        return result

    result.sha256 = compute_bytes_sha256(data)

    if _opencv_available():
        bgr = _decode_bgr_from_bytes(data)
        if bgr is not None:
            return _analyse_bgr(bgr, result)
        logger.debug("OpenCV decode failed for bytes; falling back to Pillow")

    return _analyse_with_pillow(None, data, result)


def assess_image_quality(path: str) -> QualityResult:
    """
    Assess a single image on disk (product upload path).

    Prefer OpenCV for blur, lighting, contrast, and clipped regions.
    Falls back to Pillow if OpenCV is missing or cannot decode the file.
    """
    result = QualityResult(path=path, source="file")
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

    result.sha256 = compute_file_sha256(path)

    if _opencv_available():
        try:
            bgr = _decode_bgr_from_path(path)
            if bgr is not None:
                return _analyse_bgr(bgr, result)
            logger.debug("OpenCV could not decode %s; falling back to Pillow", path)
        except Exception as e:
            logger.debug("OpenCV path assessment failed (%s); Pillow fallback", e)

    return _analyse_with_pillow(path, None, result)


def assess_batch_quality(paths: list[str]) -> tuple[list[QualityResult], bool, bool]:
    """
    Assess quality for a batch of on-disk images.

    Returns:
        (results, all_ok, any_uncertain)
    """
    results = [assess_image_quality(p) for p in paths]

    seen_hashes: dict[str, str] = {}
    for r in results:
        if r.sha256:
            if r.sha256 in seen_hashes:
                r.is_uncertain = True
                r.issues.append(f"Duplicate of {seen_hashes[r.sha256]}")
            else:
                seen_hashes[r.sha256] = r.path

    any_uncertain = any(r.is_uncertain for r in results)
    any_unusable = any(not r.is_usable for r in results)
    return results, not any_unusable and not any_uncertain, any_uncertain


def assess_upload_stream(upload: BinaryIO, *, filename: str = "upload.jpg") -> QualityResult:
    """Convenience for FastAPI UploadFile.file / SpooledTemporaryFile."""
    data = upload.read()
    if hasattr(upload, "seek"):
        try:
            upload.seek(0)
        except Exception:
            pass
    return assess_image_bytes(data, label=filename, source="upload")
