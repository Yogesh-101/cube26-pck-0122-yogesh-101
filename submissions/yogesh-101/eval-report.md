# Evaluation Report — Pack Manager

## Honest split: two evaluations

| Evaluation | What it measures | Photos? | VLM? |
|---|---|---|---|
| A. Decision engine (rules) | Deterministic SEAL/STOP given known observations | No | No |
| B. Held-out photo (this report's headline) | End-to-end order-blind Gemini + rules on frozen photos | Yes | Yes |

The Round 2 re-score correctly rejected presenting rules-only 100% as vision accuracy. This report separates them.

## B. Held-out photo evaluation (headline)

| Metric | Value |
|---|---|
| Dataset | `pck-held-out-v1` |
| Cases | 38 |
| **Decision accuracy** | **55.3%** |
| Observation exact match (SKU+qty) | 26.3% |
| **False SEAL (dangerous)** | **0** |
| False STOP (safe) | 8 |
| Cases with UNCERTAIN checks | 0 |
| Pending / VLM failures | 9 |
| Order-blind prompt | True |
| Expected order in VLM prompt | False |

### Per-scenario

| Scenario | N | Decision correct | Accuracy | Obs exact |
|---|---|---|---|---|
| ambiguous_photos | 4 | 0 | 0% | 0 |
| correct_order | 10 | 4 | 40% | 4 |
| extra_item | 4 | 4 | 100% | 0 |
| missing_item | 4 | 4 | 100% | 1 |
| multiple_identical | 4 | 1 | 25% | 1 |
| visually_similar | 4 | 0 | 0% | 0 |
| wrong_item | 4 | 4 | 100% | 2 |
| wrong_quantity | 4 | 4 | 100% | 2 |

### Method

- Live Gemini observation with order-blind prompt (catalogue only). Deterministic decision engine compares observations to the order. Ground truth frozen in manifest before this run.
- Manifest + image SHA-256 frozen before the model run (`data/eval/held_out/`).
- Labeller A = primary ground truth; Labeller B = independent second pass (same author, separate session — disclosed).
- Machine-readable run: `data/eval/results/held_out_latest.json`.

### Unit -> ground truth -> agent

| Unit | Scenario | GT decision | Agent | Match | Obs match |
|---|---|---|---|---|---|
| HO-CORRECT-04 | correct_order | seal | seal | Yes | Yes |
| HO-CORRECT-01 | correct_order | seal | seal | Yes | Yes |
| HO-CORRECT-02 | correct_order | seal | stop_and_fix | No | No |
| HO-CORRECT-03 | correct_order | seal | stop_and_fix | No | No |
| HO-CORRECT-05 | correct_order | seal | stop_and_fix | No | No |
| HO-CORRECT-06 | correct_order | seal | seal | Yes | Yes |
| HO-CORRECT-07 | correct_order | seal | stop_and_fix | No | No |
| HO-CORRECT-08 | correct_order | seal | stop_and_fix | No | No |
| HO-CORRECT-09 | correct_order | seal | stop_and_fix | No | No |
| HO-CORRECT-10 | correct_order | seal | seal | Yes | Yes |
| HO-MISSING-01 | missing_item | stop_and_fix | stop_and_fix | Yes | Yes |
| HO-MISSING-02 | missing_item | stop_and_fix | stop_and_fix | Yes | No |
| HO-MISSING-03 | missing_item | stop_and_fix | stop_and_fix | Yes | No |
| HO-MISSING-04 | missing_item | stop_and_fix | stop_and_fix | Yes | No |
| HO-WRONG-01 | wrong_item | stop_and_fix | stop_and_fix | Yes | Yes |
| HO-WRONG-02 | wrong_item | stop_and_fix | stop_and_fix | Yes | Yes |
| HO-WRONG-03 | wrong_item | stop_and_fix | stop_and_fix | Yes | No |
| HO-WRONG-04 | wrong_item | stop_and_fix | stop_and_fix | Yes | No |
| HO-EXTRA-01 | extra_item | stop_and_fix | stop_and_fix | Yes | No |
| HO-EXTRA-02 | extra_item | stop_and_fix | stop_and_fix | Yes | No |
| HO-EXTRA-03 | extra_item | stop_and_fix | stop_and_fix | Yes | No |
| HO-EXTRA-04 | extra_item | stop_and_fix | stop_and_fix | Yes | No |
| HO-QTY-01 | wrong_quantity | stop_and_fix | stop_and_fix | Yes | No |
| HO-QTY-02 | wrong_quantity | stop_and_fix | stop_and_fix | Yes | No |
| HO-QTY-03 | wrong_quantity | stop_and_fix | stop_and_fix | Yes | Yes |
| HO-QTY-04 | wrong_quantity | stop_and_fix | stop_and_fix | Yes | Yes |
| HO-MULTI-01 | multiple_identical | seal | stop_and_fix | No | No |
| HO-MULTI-02 | multiple_identical | seal | stop_and_fix | No | No |
| HO-MULTI-03 | multiple_identical | seal | seal | Yes | Yes |
| HO-MULTI-04 | multiple_identical | seal | pending | — | — |
| HO-SIMILAR-01 | visually_similar | stop_and_fix | pending | — | — |
| HO-SIMILAR-02 | visually_similar | stop_and_fix | pending | — | — |
| HO-SIMILAR-03 | visually_similar | stop_and_fix | pending | — | — |
| HO-SIMILAR-04 | visually_similar | stop_and_fix | pending | — | — |
| HO-AMBIG-01 | ambiguous_photos | stop_and_fix | pending | — | — |
| HO-AMBIG-02 | ambiguous_photos | stop_and_fix | pending | — | — |
| HO-AMBIG-03 | ambiguous_photos | stop_and_fix | pending | — | — |
| HO-AMBIG-04 | ambiguous_photos | stop_and_fix | pending | — | — |

## A. Decision engine evaluation (rules-only, not vision)

See `python -m tests.evaluation.eval_harness` and the unit tests under `tests/unit/test_decision_engine.py`.
That suite measures control logic given known observations. It is useful and required — it is **not** VLM accuracy.

## Failure modes (documented honestly)

| Mode | Behavior | Risk |
|---|---|---|
| Stacked identical items | Undercount → UNCERTAIN/STOP | Safe (no false SEAL preferred) |
| Dark/blurry photos | Quality / low confidence → STOP or pending_review | Safe |
| Visually similar products | Low confidence or wrong SKU → STOP | Safe if no false SEAL |
| VLM timeout | Pending record, operator unblocked | Safe (fail-open) |

## Limitations

1. Fixtures are real product photographs composited into open-box scenes (Unsplash), not warehouse phone captures from a live pack line.
2. Labeller B is an independent second pass by the same author — not a second human. Agreement is reported only with that disclosure.
3. Content hash is SHA-256 of canonical JSON for integrity, not a tamper-evident ledger.
