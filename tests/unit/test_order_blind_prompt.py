"""Order-blind VLM prompt: expected order must never reach the model."""

from app.domain.schemas import CatalogueProduct, OrderLine
from app.vision.gemini_client import _build_prompt


def test_prompt_excludes_expected_order():
    catalogue = [
        CatalogueProduct(sku="SKU-BOTTLE-750", name="Water Bottle 750ml"),
        CatalogueProduct(sku="SKU-CABLE-USBC", name="USB-C Charging Cable"),
    ]
    # These order lines must not appear in the vision prompt.
    _order = [OrderLine(sku="SKU-SECRET-ORDER", quantity=7)]

    prompt = _build_prompt(catalogue)

    assert "EXPECTED ORDER" not in prompt
    assert "SKU-SECRET-ORDER" not in prompt
    assert "Quantity: 7" not in prompt
    assert "SKU-BOTTLE-750" in prompt  # catalogue grounding is allowed
    assert "Do NOT use any prior knowledge of an order" in prompt


def test_prompt_is_observation_not_verification():
    prompt = _build_prompt([])
    assert "observation agent" in prompt.lower() or "Identify every distinct physical item" in prompt
