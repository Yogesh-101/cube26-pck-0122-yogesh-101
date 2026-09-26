# Pack Manager — Evaluation Report

## Summary

| Metric | Value |
|---|---|
| Total cases | 51 |
| Correct decisions | 51 |
| **Decision accuracy** | **100.0%** |
| **False PASS (dangerous)** | **0** |
| False STOP (safe) | 0 |
| UNCERTAIN verdicts | 7 |
| Pending review | 7 |

## Per-Scenario Accuracy

| Scenario | Total | Correct | Accuracy |
|---|---|---|---|
| ambiguous_photos | 5 | 5 | 100% |
| correct_order | 10 | 10 | 100% |
| extra_item | 6 | 6 | 100% |
| missing_item | 8 | 8 | 100% |
| multiple_identical | 5 | 5 | 100% |
| visually_similar | 5 | 5 | 100% |
| wrong_item | 6 | 6 | 100% |
| wrong_quantity | 6 | 6 | 100% |

## Per-Check Metrics

| Check | TP | FP | FN | TN | UNCERTAIN |
|---|---|---|---|---|---|
| items_present | 14 | 0 | 0 | 10 | 0 |
| no_extra_items | 12 | 0 | 0 | 10 | 0 |
| quantity_match | 6 | 0 | 0 | 10 | 0 |

## Organiser Sample Data Evaluation (pack_sample.csv)

29 units from the official CSV, with ground truth derived from `order_lines` vs `observed_in_box`.

| Metric | Value |
|---|---|
| Total rows | 29 |
| Correct decisions | **29/29** |
| False PASS | **0** |
| Correct orders (SEAL) | 25 |
| Extra item detected (STOP) | 3 |
| Wrong item detected (STOP) | 1 |

### Deliberately Wrong Operator Verdicts (caught)

The CSV contains 2 rows where `operator_verdict` is intentionally wrong:

| Record | Operator Said | Ground Truth | Engine Decision | Issue |
|---|---|---|---|---|
| PCK-0034 | seal | stop_and_fix | **STOP_AND_FIX** | Extra USB-C cable in box |
| PCK-0044 | seal | stop_and_fix | **STOP_AND_FIX** | Expected candle, got bottle |

Our decision engine is **independent of operator labels** — it reasons from expected vs observed data only. This satisfies the requirement that operator verdicts are "sometimes wrong on purpose" and the engine must not blindly trust them.

### Findings

- PCK-0034: Order was `SKU-CANDLE-3:2;SKU-BOTTLE-750:1` but box contained an additional `SKU-CABLE-USBC:1`. Operator incorrectly approved.
- PCK-0044: Order was `SKU-CANDLE-3:1` but box contained `SKU-BOTTLE-750:1` instead. Operator incorrectly approved.
- Both are raised as findings per the honesty rules.

## Methodology

- **Decision engine evaluation (51 synthetic cases):** deterministic test data covering all 8 official scenarios, no VLM
- **Sample data evaluation (29 CSV cases):** ground truth from official `pack_sample.csv`, including deliberately wrong operator verdicts
- False PASS is the headline safety metric (approving incorrect packages)
- UNCERTAIN correctly routes to human review, never auto-seals
- Per-check FP and FN reported separately (honesty rule)
- **Total evaluated: 80 cases, 0 false PASS**
