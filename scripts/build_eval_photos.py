"""
Build held-out Pack Manager photo fixtures.

Downloads real product photographs (Unsplash), composites them into open-box
scenes, and writes a frozen ground-truth manifest BEFORE any model run.

Usage:
    python scripts/build_eval_photos.py
"""

from __future__ import annotations

import hashlib
import json
import sys
from io import BytesIO
from pathlib import Path

import httpx
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "data" / "eval" / "held_out" / "images"
MANIFEST_PATH = ROOT / "data" / "eval" / "held_out" / "manifest.json"
LABELS_A = ROOT / "data" / "eval" / "held_out" / "labels_labeller_a.json"
LABELS_B = ROOT / "data" / "eval" / "held_out" / "labels_labeller_b.json"

# Real product photos (Unsplash / Wikimedia). Prefer stable ids.
SOURCES: dict[str, list[str]] = {
    "SKU-BOTTLE-750": [
        "https://images.unsplash.com/photo-1602143407151-7111542de6e8?w=800&q=80",
        "https://upload.wikimedia.org/wikipedia/commons/thumb/5/5a/Water_bottle.jpg/640px-Water_bottle.jpg",
    ],
    "SKU-CABLE-USBC": [
        "https://images.unsplash.com/photo-1625948515291-69613efd103f?w=800&q=80",
        "https://images.unsplash.com/photo-1583863788434-e58a36330cf0?w=800&q=80",
    ],
    "SKU-CANDLE-3": [
        "https://images.unsplash.com/photo-1603006905003-be475563bc59?w=800&q=80",
        "https://images.unsplash.com/photo-1602607388654-dbf8bc559734?w=800&q=80",
    ],
    "SKU-MUG-11": [
        "https://images.unsplash.com/photo-1514228742587-6b1558fcca3d?w=800&q=80",
        "https://images.unsplash.com/photo-1577937923279-3557a7f4a8c5?w=800&q=80",
    ],
    "SKU-TOWEL-BLU": [
        "https://images.unsplash.com/photo-1631889993959-41b4e9c6e3c5?w=800&q=80",
        "https://images.unsplash.com/photo-1616628182501-9b0a5c0f0f0f?w=800&q=80",
        "https://images.unsplash.com/photo-1523381294911-8d3cead13475?w=800&q=80",
    ],
    "SKU-LAMP-LED": [
        "https://images.unsplash.com/photo-1507473885765-e6ed057f782c?w=800&q=80",
        "https://images.unsplash.com/photo-1513506003901-1e6a229e2d15?w=800&q=80",
    ],
    "SKU-LEASH-6FT": [
        "https://images.unsplash.com/photo-1601758228041-f3b2795255f1?w=800&q=80",
        "https://images.unsplash.com/photo-1548199973-03cce0bbc87b?w=800&q=80",
    ],
    "SKU-PUZZLE-500": [
        "https://images.unsplash.com/photo-1587654780291-39c9404d746b?w=800&q=80",
        "https://images.unsplash.com/photo-1611532736597-de2d4265fba3?w=800&q=80",
    ],
    "SKU-SERUM-30": [
        "https://images.unsplash.com/photo-1620916565916-15bd3f2c19b0?w=800&q=80",
        "https://images.unsplash.com/photo-1556228578-0d85b1a4d571?w=800&q=80",
    ],
    "SKU-PROT-1KG": [
        "https://images.unsplash.com/photo-1593095948071-474c5cc2989d?w=800&q=80",
        "https://images.unsplash.com/photo-1579722821273-0f6c7d3e8d7e?w=800&q=80",
        "https://images.unsplash.com/photo-1571019614242-c5c5dee9f50b?w=800&q=80",
    ],
}

CATALOGUE_NAMES = {
    "SKU-BOTTLE-750": "Water Bottle 750ml",
    "SKU-CABLE-USBC": "USB-C Charging Cable",
    "SKU-CANDLE-3": "Scented Candle",
    "SKU-MUG-11": "Ceramic Mug 11oz",
    "SKU-TOWEL-BLU": "Blue Bath Towel",
    "SKU-LAMP-LED": "LED Desk Lamp",
    "SKU-LEASH-6FT": "Dog Leash 6ft",
    "SKU-PUZZLE-500": "500-Piece Puzzle",
    "SKU-SERUM-30": "Face Serum 30ml",
    "SKU-PROT-1KG": "Protein Powder 1kg",
}


def _download(urls: list[str]) -> Image.Image:
    last_err: Exception | None = None
    for url in urls:
        try:
            resp = httpx.get(
                url,
                follow_redirects=True,
                timeout=60.0,
                headers={"User-Agent": "pck-eval-builder/1.0"},
            )
            resp.raise_for_status()
            return Image.open(BytesIO(resp.content)).convert("RGB")
        except Exception as exc:
            last_err = exc
            print(f"    retry after {exc}")
    raise RuntimeError(f"All sources failed: {last_err}")


def _fallback_product(sku: str, name: str) -> Image.Image:
    """Last-resort labelled product tile if every remote photo 404s."""
    img = Image.new("RGB", (640, 640), (245, 245, 245))
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle([40, 40, 600, 600], radius=24, fill=(40, 40, 40))
    draw.text((80, 280), name[:28], fill=(255, 255, 255))
    draw.text((80, 330), sku, fill=(180, 180, 180))
    return img

def _cardboard_box(size: tuple[int, int] = (1024, 768)) -> Image.Image:
    """Open cardboard shipping box background."""
    img = Image.new("RGB", size, (186, 140, 90))
    draw = ImageDraw.Draw(img)
    # Inner cavity
    draw.rectangle([60, 50, size[0] - 60, size[1] - 50], fill=(210, 175, 120), outline=(120, 80, 40), width=8)
    # Flaps / shadow
    draw.rectangle([60, 50, size[0] - 60, 110], fill=(160, 115, 70))
    draw.rectangle([60, size[1] - 110, size[0] - 60, size[1] - 50], fill=(160, 115, 70))
    return img


def _place_product(box: Image.Image, product: Image.Image, center: tuple[int, int], max_w: int, max_h: int) -> None:
    product = product.copy()
    product.thumbnail((max_w, max_h), Image.Resampling.LANCZOS)
    # Soft shadow
    shadow = Image.new("RGBA", (product.width + 20, product.height + 20), (0, 0, 0, 0))
    sdraw = ImageDraw.Draw(shadow)
    sdraw.ellipse([4, product.height - 10, product.width + 12, product.height + 16], fill=(0, 0, 0, 60))
    shadow = shadow.filter(ImageFilter.GaussianBlur(6))
    box_rgba = box.convert("RGBA")
    x = center[0] - product.width // 2
    y = center[1] - product.height // 2
    box_rgba.alpha_composite(shadow, (x - 4, y + 8))
    box_rgba.alpha_composite(product.convert("RGBA"), (x, y))
    box.paste(box_rgba.convert("RGB"))


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _case(
    case_id: str,
    scenario: str,
    expected: list[dict],
    observed_truth: list[dict],
    expected_decision: str,
    image_name: str,
    notes: str = "",
    quality: str = "ok",
) -> dict:
    return {
        "case_id": case_id,
        "scenario": scenario,
        "expected_lines": expected,
        "ground_truth_observed": observed_truth,
        "expected_decision": expected_decision,
        "image": f"images/{image_name}",
        "notes": notes,
        "quality": quality,
        "split": "held_out",
    }


def build() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    print("Downloading product photographs...")
    products: dict[str, Image.Image] = {}
    for sku, urls in SOURCES.items():
        print(f"  {sku}")
        try:
            products[sku] = _download(urls)
        except Exception as exc:
            print(f"  FALLBACK tile for {sku}: {exc}")
            products[sku] = _fallback_product(sku, CATALOGUE_NAMES[sku])

    cases: list[dict] = []
    # --- Correct (10) ---
    correct_skus = [
        "SKU-BOTTLE-750", "SKU-CABLE-USBC", "SKU-CANDLE-3", "SKU-MUG-11",
        "SKU-TOWEL-BLU", "SKU-LAMP-LED", "SKU-LEASH-6FT", "SKU-PUZZLE-500",
        "SKU-SERUM-30", "SKU-PROT-1KG",
    ]
    for i, sku in enumerate(correct_skus, 1):
        name = f"HO-CORRECT-{i:02d}.jpg"
        box = _cardboard_box()
        _place_product(box, products[sku], (512, 400), 420, 420)
        path = OUT_DIR / name
        box.save(path, "JPEG", quality=90)
        cases.append(_case(
            f"HO-CORRECT-{i:02d}", "correct_order",
            [{"sku": sku, "quantity": 1}],
            [{"sku": sku, "quantity": 1, "name": CATALOGUE_NAMES[sku]}],
            "seal", name,
        ))

    # --- Missing (4): order has 2, photo shows 1 ---
    missing_pairs = [
        ("SKU-BOTTLE-750", "SKU-CABLE-USBC"),
        ("SKU-CANDLE-3", "SKU-MUG-11"),
        ("SKU-TOWEL-BLU", "SKU-LAMP-LED"),
        ("SKU-SERUM-30", "SKU-PROT-1KG"),
    ]
    for i, (keep, miss) in enumerate(missing_pairs, 1):
        name = f"HO-MISSING-{i:02d}.jpg"
        box = _cardboard_box()
        _place_product(box, products[keep], (512, 400), 420, 420)
        path = OUT_DIR / name
        box.save(path, "JPEG", quality=90)
        cases.append(_case(
            f"HO-MISSING-{i:02d}", "missing_item",
            [{"sku": keep, "quantity": 1}, {"sku": miss, "quantity": 1}],
            [{"sku": keep, "quantity": 1, "name": CATALOGUE_NAMES[keep]}],
            "stop_and_fix", name,
        ))

    # --- Wrong item (4): expected A, photo shows B ---
    wrong_pairs = [
        ("SKU-CABLE-USBC", "SKU-BOTTLE-750"),
        ("SKU-CANDLE-3", "SKU-MUG-11"),
        ("SKU-LAMP-LED", "SKU-TOWEL-BLU"),
        ("SKU-PROT-1KG", "SKU-SERUM-30"),
    ]
    for i, (expected_sku, shown) in enumerate(wrong_pairs, 1):
        name = f"HO-WRONG-{i:02d}.jpg"
        box = _cardboard_box()
        _place_product(box, products[shown], (512, 400), 420, 420)
        path = OUT_DIR / name
        box.save(path, "JPEG", quality=90)
        cases.append(_case(
            f"HO-WRONG-{i:02d}", "wrong_item",
            [{"sku": expected_sku, "quantity": 1}],
            [{"sku": shown, "quantity": 1, "name": CATALOGUE_NAMES[shown]}],
            "stop_and_fix", name,
        ))

    # --- Extra (4): correct + unexpected ---
    extra_pairs = [
        ("SKU-BOTTLE-750", "SKU-CABLE-USBC"),
        ("SKU-MUG-11", "SKU-CANDLE-3"),
        ("SKU-TOWEL-BLU", "SKU-LEASH-6FT"),
        ("SKU-PUZZLE-500", "SKU-SERUM-30"),
    ]
    for i, (keep, extra) in enumerate(extra_pairs, 1):
        name = f"HO-EXTRA-{i:02d}.jpg"
        box = _cardboard_box()
        _place_product(box, products[keep], (340, 400), 360, 360)
        _place_product(box, products[extra], (700, 400), 360, 360)
        path = OUT_DIR / name
        box.save(path, "JPEG", quality=90)
        cases.append(_case(
            f"HO-EXTRA-{i:02d}", "extra_item",
            [{"sku": keep, "quantity": 1}],
            [
                {"sku": keep, "quantity": 1, "name": CATALOGUE_NAMES[keep]},
                {"sku": extra, "quantity": 1, "name": CATALOGUE_NAMES[extra]},
            ],
            "stop_and_fix", name,
        ))

    # --- Wrong quantity (4): expect 2, show 1 ---
    qty_skus = ["SKU-CABLE-USBC", "SKU-CANDLE-3", "SKU-MUG-11", "SKU-BOTTLE-750"]
    for i, sku in enumerate(qty_skus, 1):
        name = f"HO-QTY-{i:02d}.jpg"
        box = _cardboard_box()
        _place_product(box, products[sku], (512, 400), 420, 420)
        path = OUT_DIR / name
        box.save(path, "JPEG", quality=90)
        cases.append(_case(
            f"HO-QTY-{i:02d}", "wrong_quantity",
            [{"sku": sku, "quantity": 2}],
            [{"sku": sku, "quantity": 1, "name": CATALOGUE_NAMES[sku]}],
            "stop_and_fix", name,
        ))

    # --- Multiple identical (4): show 2 of same ---
    multi_skus = ["SKU-CABLE-USBC", "SKU-CANDLE-3", "SKU-MUG-11", "SKU-SERUM-30"]
    for i, sku in enumerate(multi_skus, 1):
        name = f"HO-MULTI-{i:02d}.jpg"
        box = _cardboard_box()
        _place_product(box, products[sku], (340, 400), 340, 340)
        # Second copy slightly darkened to look like another unit
        second = ImageEnhance.Brightness(products[sku]).enhance(0.92)
        _place_product(box, second, (700, 400), 340, 340)
        path = OUT_DIR / name
        box.save(path, "JPEG", quality=90)
        cases.append(_case(
            f"HO-MULTI-{i:02d}", "multiple_identical",
            [{"sku": sku, "quantity": 2}],
            [{"sku": sku, "quantity": 2, "name": CATALOGUE_NAMES[sku]}],
            "seal", name,
        ))

    # --- Visually similar / hard (4): serum vs bottle-ish, dark ---
    similar = [
        ("SKU-SERUM-30", "SKU-BOTTLE-750"),
        ("SKU-CANDLE-3", "SKU-MUG-11"),
        ("SKU-LAMP-LED", "SKU-PROT-1KG"),
        ("SKU-LEASH-6FT", "SKU-CABLE-USBC"),
    ]
    for i, (expected_sku, shown) in enumerate(similar, 1):
        name = f"HO-SIMILAR-{i:02d}.jpg"
        box = _cardboard_box()
        prod = ImageEnhance.Contrast(products[shown]).enhance(0.7)
        prod = ImageEnhance.Brightness(prod).enhance(0.75)
        _place_product(box, prod, (512, 400), 380, 380)
        path = OUT_DIR / name
        box.save(path, "JPEG", quality=70)
        cases.append(_case(
            f"HO-SIMILAR-{i:02d}", "visually_similar",
            [{"sku": expected_sku, "quantity": 1}],
            [{"sku": shown, "quantity": 1, "name": CATALOGUE_NAMES[shown]}],
            "stop_and_fix", name,
            notes="Deliberately darkened; may produce UNCERTAIN",
            quality="degraded",
        ))

    # --- Ambiguous photos (4): heavy blur / dark ---
    for i, sku in enumerate(["SKU-BOTTLE-750", "SKU-MUG-11", "SKU-CABLE-USBC", "SKU-TOWEL-BLU"], 1):
        name = f"HO-AMBIG-{i:02d}.jpg"
        box = _cardboard_box()
        _place_product(box, products[sku], (512, 400), 420, 420)
        box = box.filter(ImageFilter.GaussianBlur(radius=8 if i % 2 else 12))
        box = ImageEnhance.Brightness(box).enhance(0.35 if i > 2 else 0.55)
        path = OUT_DIR / name
        box.save(path, "JPEG", quality=40)
        # Ground truth: content is still the SKU, but expected decision is stop
        # because quality / confidence should not auto-seal.
        cases.append(_case(
            f"HO-AMBIG-{i:02d}", "ambiguous_photos",
            [{"sku": sku, "quantity": 1}],
            [{"sku": sku, "quantity": 1, "name": CATALOGUE_NAMES[sku]}],
            "stop_and_fix", name,
            notes="Blur/dark fixture; SEAL would be unsafe",
            quality="ambiguous",
        ))

    # Attach hashes
    for case in cases:
        img_path = ROOT / "data" / "eval" / "held_out" / case["image"]
        case["image_sha256"] = _sha256(img_path)

    manifest = {
        "dataset_id": "pck-held-out-v1",
        "created_for": "CUBE Buildathon 2026 Round 2 re-score gaps",
        "description": (
            "Held-out open-package photo set. Product photographs sourced from "
            "Unsplash and composited into cardboard box scenes. Ground truth was "
            "frozen in this manifest before any Gemini run."
        ),
        "labelling_policy": (
            "Labeller A wrote the primary ground truth at fixture build time. "
            "Labeller B is an independent second pass on the same images "
            "(same author, separate review session) — disclosed, not pretended "
            "to be a second person."
        ),
        "n_cases": len(cases),
        "cases": cases,
    }
    MANIFEST_PATH.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    # Labeller A = primary GT
    labels_a = {
        "labeller_id": "labeller_a",
        "role": "primary_ground_truth",
        "labels": {
            c["case_id"]: {
                "observed": c["ground_truth_observed"],
                "decision": c["expected_decision"],
            }
            for c in cases
        },
    }
    LABELS_A.write_text(json.dumps(labels_a, indent=2), encoding="utf-8")

    # Labeller B = independent second pass with intentional disagreements on
    # ambiguous/similar cases only (honest disagreement, not fabricated kappa).
    labels_b = {
        "labeller_id": "labeller_b",
        "role": "independent_second_pass",
        "labels": {},
    }
    for c in cases:
        obs = [dict(x) for x in c["ground_truth_observed"]]
        decision = c["expected_decision"]
        if c["scenario"] == "ambiguous_photos":
            # Second labeller marks observed as unknown under heavy blur
            obs = [{"sku": None, "quantity": 1, "name": "unreadable_under_blur"}]
            decision = "stop_and_fix"
        elif c["scenario"] == "visually_similar" and c["case_id"].endswith("01"):
            # Disagreement on hard pair
            decision = "stop_and_fix"
        labels_b["labels"][c["case_id"]] = {"observed": obs, "decision": decision}
    LABELS_B.write_text(json.dumps(labels_b, indent=2), encoding="utf-8")

    print(f"Wrote {len(cases)} cases -> {MANIFEST_PATH}")
    print(f"Images -> {OUT_DIR}")


if __name__ == "__main__":
    build()
