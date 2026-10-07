"""Held-out photo dataset integrity — no live API required."""

import json
from pathlib import Path

from app.domain.schemas import CatalogueProduct
from app.vision.gemini_client import _build_prompt

ROOT = Path(__file__).resolve().parents[2]
HELD_OUT = ROOT / "data" / "eval" / "held_out"
MANIFEST = HELD_OUT / "manifest.json"
CATALOGUE = ROOT / "data" / "eval" / "catalogue.json"


def test_held_out_manifest_exists_with_photos():
    assert MANIFEST.exists(), "Run scripts/build_eval_photos.py"
    data = json.loads(MANIFEST.read_text(encoding="utf-8"))
    assert data["n_cases"] >= 30
    assert len(data["cases"]) == data["n_cases"]
    for case in data["cases"]:
        img = HELD_OUT / case["image"]
        assert img.exists(), case["case_id"]
        assert case.get("image_sha256")
        assert case["expected_decision"] in ("seal", "stop_and_fix")
        assert case["ground_truth_observed"]


def test_prompt_order_blind_against_catalogue():
    catalogue = [CatalogueProduct(**row) for row in json.loads(CATALOGUE.read_text(encoding="utf-8"))]
    prompt = _build_prompt(catalogue)
    assert "EXPECTED ORDER" not in prompt
    assert "Do NOT use any prior knowledge of an order" in prompt


def test_labeller_files_disclosed():
    a = json.loads((HELD_OUT / "labels_labeller_a.json").read_text(encoding="utf-8"))
    b = json.loads((HELD_OUT / "labels_labeller_b.json").read_text(encoding="utf-8"))
    assert a["labeller_id"] == "labeller_a"
    assert b["labeller_id"] == "labeller_b"
    policy = json.loads(MANIFEST.read_text(encoding="utf-8"))["labelling_policy"].lower()
    assert "disclosed" in policy or "same author" in policy
