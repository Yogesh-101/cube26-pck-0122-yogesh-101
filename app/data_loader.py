"""
PCK Pack Manager — Sample Data Loader

Parses the organiser's pack_sample.csv into our domain models.
Extracts ground truth from the expected vs observed columns.

IMPORTANT (Engineering Rule 5): The requirement flags and fee amounts
in the CSV are dummy values. We use them for schema design and testing,
NOT as authoritative rules.
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Optional

from app.domain.schemas import (
    CatalogueProduct,
    Channel,
    Decision,
    Order,
    OrderLine,
    ObservedItem,
)


# ---------------------------------------------------------------------------
# Product catalogue derived from the sample CSV SKUs
# ---------------------------------------------------------------------------

SAMPLE_CATALOGUE: dict[str, CatalogueProduct] = {
    "SKU-CABLE-USBC": CatalogueProduct(
        sku="SKU-CABLE-USBC", name="USB-C Charging Cable", variant="1m",
        description="Braided USB-C to USB-C charging cable, 1 meter",
    ),
    "SKU-BOTTLE-750": CatalogueProduct(
        sku="SKU-BOTTLE-750", name="Water Bottle 750ml", variant="Steel",
        description="Stainless steel insulated water bottle, 750ml",
    ),
    "SKU-PUZZLE-500": CatalogueProduct(
        sku="SKU-PUZZLE-500", name="Jigsaw Puzzle 500pc", variant="Landscape",
        description="500-piece jigsaw puzzle, landscape theme",
    ),
    "SKU-TOWEL-BLU": CatalogueProduct(
        sku="SKU-TOWEL-BLU", name="Blue Towel", variant="Bath",
        description="Blue cotton bath towel",
    ),
    "SKU-SERUM-30": CatalogueProduct(
        sku="SKU-SERUM-30", name="Face Serum 30ml", variant="Vitamin C",
        description="Vitamin C face serum, 30ml bottle",
    ),
    "SKU-PROT-1KG": CatalogueProduct(
        sku="SKU-PROT-1KG", name="Protein Powder 1kg", variant="Chocolate",
        description="Whey protein powder, chocolate flavor, 1kg tub",
    ),
    "SKU-CANDLE-3": CatalogueProduct(
        sku="SKU-CANDLE-3", name="Scented Candle 3-Pack", variant="Lavender",
        description="Set of 3 lavender scented candles",
    ),
    "SKU-LAMP-LED": CatalogueProduct(
        sku="SKU-LAMP-LED", name="LED Desk Lamp", variant="White",
        description="Adjustable LED desk lamp, white, USB powered",
    ),
    "SKU-MUG-11": CatalogueProduct(
        sku="SKU-MUG-11", name="Ceramic Mug 11oz", variant="White",
        description="White ceramic mug, 11oz capacity",
    ),
    "SKU-LEASH-6FT": CatalogueProduct(
        sku="SKU-LEASH-6FT", name="Dog Leash 6ft", variant="Black",
        description="Black nylon dog leash, 6 feet",
    ),
}


def get_catalogue() -> list[CatalogueProduct]:
    """Return the full sample product catalogue."""
    return list(SAMPLE_CATALOGUE.values())


def get_catalogue_for_skus(skus: list[str]) -> list[CatalogueProduct]:
    """Return catalogue entries only for the given SKUs."""
    return [SAMPLE_CATALOGUE[s] for s in skus if s in SAMPLE_CATALOGUE]


# ---------------------------------------------------------------------------
# CSV Parser
# ---------------------------------------------------------------------------

def parse_sku_qty_string(s: str) -> list[tuple[str, int]]:
    """Parse 'SKU-X:2;SKU-Y:1' format into [(sku, qty), ...]."""
    items = []
    for part in s.split(";"):
        part = part.strip()
        if not part:
            continue
        if ":" in part:
            sku, qty_str = part.rsplit(":", 1)
            items.append((sku.strip(), int(qty_str.strip())))
        else:
            items.append((part.strip(), 1))
    return items


def load_sample_data(csv_path: str | Path | None = None) -> list[dict]:
    """
    Load pack_sample.csv and return structured records.

    Each record contains:
      - record_id, unit_id, org_id, order_id, channel
      - expected_lines: list of OrderLine
      - observed_items: list of ObservedItem (ground truth from CSV)
      - operator_verdict: what the human said (sometimes deliberately wrong)
      - ground_truth_decision: what the correct decision should be
      - discrepancy_type: type of issue if any
    """
    if csv_path is None:
        csv_path = Path(__file__).parent.parent / "data" / "pack_sample.csv"
    else:
        csv_path = Path(csv_path)

    records = []

    with open(csv_path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            # Parse expected order lines
            expected_pairs = parse_sku_qty_string(row["order_lines"])
            expected_lines = [
                OrderLine(sku=sku, quantity=qty) for sku, qty in expected_pairs
            ]

            # Parse observed items (ground truth)
            observed_pairs = parse_sku_qty_string(row["observed_in_box"])
            observed_items = [
                ObservedItem(
                    sku=sku,
                    name=SAMPLE_CATALOGUE[sku].name if sku in SAMPLE_CATALOGUE else sku,
                    observed_quantity=qty,
                    confidence=0.92,  # simulated high confidence for ground truth
                )
                for sku, qty in observed_pairs
            ]

            # Derive ground truth decision
            expected_map = {sku: qty for sku, qty in expected_pairs}
            observed_map = {sku: qty for sku, qty in observed_pairs}

            discrepancy_type = "correct"
            if expected_map != observed_map:
                missing = set(expected_map) - set(observed_map)
                extra = set(observed_map) - set(expected_map)
                qty_mismatch = {
                    s for s in expected_map
                    if s in observed_map and expected_map[s] != observed_map[s]
                }
                if missing and extra:
                    discrepancy_type = "wrong_item"
                elif missing:
                    discrepancy_type = "missing_item"
                elif extra:
                    discrepancy_type = "extra_item"
                elif qty_mismatch:
                    discrepancy_type = "wrong_quantity"

            ground_truth = "seal" if expected_map == observed_map else "stop_and_fix"

            # Flag deliberately wrong operator verdicts
            operator_verdict = row["operator_verdict"]
            operator_correct = operator_verdict == ground_truth

            records.append({
                "record_id": row["record_id"],
                "unit_id": row["unit_id"],
                "org_id": row["org_id"],
                "order_id": row["order_id"],
                "channel": row["channel"],
                "expected_lines": expected_lines,
                "observed_items": observed_items,
                "operator_verdict": operator_verdict,
                "ground_truth_decision": ground_truth,
                "discrepancy_type": discrepancy_type,
                "operator_correct": operator_correct,
                "photo_refs": row.get("photo_refs", ""),
                "operator_id": row.get("operator_id", ""),
                "captured_at": row.get("captured_at", ""),
            })

    return records


def build_order_from_record(record: dict) -> Order:
    """Build an Order domain object from a parsed CSV record."""
    return Order(
        order_id=record["order_id"],
        unit_id=record["unit_id"],
        org_id=record["org_id"],
        channel=record["channel"],
        lines=record["expected_lines"],
    )
