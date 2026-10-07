# Pack Manager — AI Packing Verification Agent

**CUBE Buildathon 2026 · Track 03 · Pack Manager**
**Round 2 Individual Build — Yogesh Macherla**

> Verify every order before the box is sealed. From a photograph of the open package, determine: does this box contain exactly what the customer ordered?

---

## Problem understanding

A picker assembles an order and closes the box. If the wrong item or quantity goes in, the customer gets a mis-ship: a refund, a return, a replacement shipment, and a bad review. Manual checking doesn't scale.

Before anything is sealed, Pack Manager has to say what is in the open package compared with the order:

- items present
- missing items
- incorrect items (an expected SKU is absent and a different SKU is in the box)
- incorrect quantities
- unexpected extra items

The operational decision is **SEAL** or **STOP & FIX**. **UNCERTAIN** is a valid result when the photograph or the match is ambiguous. The system must not invent a pass, and it must not invent items that are not visible or documented.

Inputs are the order, an optional product catalogue (SKU, ASIN, name, aliases), and photographs of the open package. Outputs are the detected items, expected lines, expected versus observed quantities, the discrepancies above, the decision, and a supporting evidence record.

**Who it is for:** sellers fulfilling their own orders, and 3PLs. Channels in the schema are `amazon_mfn`, `shopify`, `walmart`, and `3pl_client`. Amazon FBA is out of scope because Amazon packs those orders.

The cases the engine can represent are a correct order (SEAL), a missing item, a wrong item, an extra item, a wrong quantity, several identical products, visually similar products (low confidence), and ambiguous photographs (UNCERTAIN / `pending_review`, never a low-confidence pass).

### Scope

This agent serves **merchant-fulfilled and 3PL orders only**. FBA orders are packed by Amazon and are explicitly out of scope.

### Position in the Chain

```
Receiving → Prep → [Pack Manager] → Returns → Recovery
```

Pack Manager is step 3 of 5. Our evidence records use `unit_id` as the join key shared across all five managers. Our output is consumed by:
- **Returns Manager**: knows what was actually sent (to compare with what came back)
- **Recovery Manager**: uses our evidence for buyer disputes, empty-box and wrong-item claims

---

## Solution overview

```
Order + Catalogue → Input Validation → Image Quality Gate → Gemini VLM (single batched call)
    → Schema Validation → Decision Engine → Evidence Record → Operator UI
```

**Architecture principle**: AI provides observations; deterministic code provides control. The VLM identifies products and counts items. A rule engine makes the final operational decision. UNCERTAIN is a first-class outcome — the system never forces a conclusion from ambiguous evidence.

### Key Design Decisions

| Decision | Rationale |
|---|---|
| Single batched VLM call per unit | Engineering Rule 2: one call carrying all checks, not one per check |
| **Order-blind VLM prompt** | Expected order never sent to Gemini — closes re-score "order in prompt" gap |
| Deterministic decision engine | Final SEAL/STOP never depends on raw LLM output |
| Held-out photo eval | `data/eval/held_out/` real product photos; results in `data/eval/results/` |
| UNCERTAIN → human review | Never auto-seal when evidence is insufficient |
| Fail-open on errors | Engineering Rule 3: model timeout saves pending record, never blocks operator |
| Org-scoped queries | Engineering Rule 1: tenancy isolation tested with two demo orgs |
| Content hash on evidence | SHA-256 of canonical JSON. Detects accidental change. It is not a tamper-evident or anchored ledger |
| Bounded Gemini retry | Transient 503/429 honour `retryDelay`; daily-quota delays fail fast |

---

## Setup instructions

### Prerequisites

- Python 3.11+
- A Gemini API key ([Google AI Studio](https://aistudio.google.com/apikey)). The vision step will not run without `GEMINI_API_KEY`.

### Installation

```bash
git clone https://github.com/Yogesh-101/cube26-pck-0122-yogesh-101.git
cd cube26-pck-0122-yogesh-101

python -m pip install -r requirements.txt
```

Copy the example environment file and set the key. Do not commit `.env`.

```bash
cp .env.example .env
```

On Windows PowerShell:

```powershell
Copy-Item .env.example .env
```

Open `.env` and set `GEMINI_API_KEY`. The model name defaults to `gemini-2.5-flash` (`GEMINI_MODEL`). Optional retry knobs are `VLM_MAX_RETRIES` (default 2) and `VLM_RETRY_BASE_SECONDS` (default 1.0). `.env.example` contains placeholders only.

### Run

```bash
python -m uvicorn app.main:app --reload --port 8000
```

Open http://localhost:8000 in your browser.

## Usage instructions

The header org switcher is the tenant. The dashboard and every results page read that value and send it as `org_id`. A second organisation sees none of the first organisation's rows. Opening another organisation's inspection id shows **Inspection not found for this organisation**, not that tenant's data.

1. **Dashboard** (`/`). Lists inspections for the active org. Outcome badges are SEAL, STOP & FIX, NEEDS REVIEW (`pending_review`), and PENDING. Filter and search are client-side on that list.
2. **New inspection** (`/inspect`). Enter order id, unit id, channel, and at least one SKU line. Add photographs of the open box. Catalogue JSON is optional; if present it is sent as `catalogue_json` and reaches the vision prompt. Submit calls `POST /api/v1/verify/json`. The page does not submit until order id, unit id, one line, and one photo are present.
3. **Results** (`/results/{inspection_id}`). Shows the decision in force, the captured photos, detected versus expected quantities, and each check. Photos load from an org-scoped URL; another organisation gets 404, not the file. Reconciliation labels are match, missing, short, over, extra, and unknown. When the model never ran (`pending`), those lines read **Not checked**, not Missing, and the banner includes the saved reason (for example a Gemini 503). A `pending_review` record shows the uncertainty detail: reason code, what is known, what is unknown, missing evidence, and the recommended action.
4. **Override**. On a STOP & FIX, NEEDS REVIEW, or PENDING result, record a new decision of SEAL or STOP & FIX. Operator id and reason are required. The agent's original `outcome` stays as stored. The override row keeps the original verdict, the new verdict, the operator id, and the reason. The page shows both.

### Run Tests

```bash
python -m pytest tests/ -v
```

### Run Evaluation

```bash
# A) Rules-only decision engine (not vision accuracy)
python -m tests.evaluation.eval_harness --output docs/EVALUATION.md

# B) Held-out photo eval — order-blind Gemini (headline for re-score)
python scripts/build_eval_photos.py          # once: fixtures + frozen GT
python scripts/run_photo_eval.py --model gemini-3.8-flash --sleep 20
python scripts/run_photo_eval.py --model gemini-3.8-flash --sleep 20 --resume
```

See `data/eval/README.md` and `docs/EVALUATION.md`.

### Docker

```bash
docker compose up --build
```

---

## API

### `POST /api/v1/verify/json`

Verify a package. Accepts form fields + image uploads.

**Form fields:**
- `order_id` — Order identifier
- `unit_id` — Cross-manager join key (UNIT-xxxx)
- `org_id` — Tenant identifier
- `channel` — `amazon_mfn` | `shopify` | `walmart` | `3pl_client`
- `order_lines_json` — JSON array: `[{"sku": "SKU-001", "quantity": 2}]`
- `catalogue_json` — Optional catalogue for grounding
- `images` — Package photograph(s)

**Response:**
```json
{
  "inspection_id": "uuid",
  "decision": "seal | stop_and_fix",
  "status": "completed | pending | pending_review",
  "checks": [...],
  "observed_items": [...],
  "evidence_record_id": "PCK-...",
  "content_hash": "sha256..."
}
```

### `GET /api/v1/inspections?org_id=...`

List inspections (org-scoped).

### `GET /api/v1/inspections/{id}?org_id=...`

Get full inspection detail with the evidence record. `outcome` stays the agent's original verdict. The body also includes `overrides` and `current_decision` (the latest override, or the agent decision when there is none).

### `GET /api/v1/inspections/{id}/images/{image_id}?org_id=...`

Serve one captured photo for that organisation. The inspection lookup is org-scoped, the image id must be listed on that record, and the file must resolve inside the storage root. Another organisation receives 404, not the file.

### `POST /api/v1/inspections/{id}/override?org_id=...`

Human override — preserves original AI decision.

### `GET /api/v1/inspections/{id}/evidence?org_id=...`

Export the evidence record in the official CUBE contract format. Designed for interoperability with Returns Manager and Recovery Manager (Round 3).

### `GET /api/v1/evidence/by-unit/{unit_id}?org_id=...`

Look up evidence by `unit_id` — the cross-manager join key. Other managers use this to find what Pack Manager saw and decided.

### `GET /api/v1/health`

Health check.

---

## Evidence Contract

Every inspection produces a structured evidence record compatible with the official CUBE contract:

```
record_id, schema_version, organization_id, client_id, agent, subject,
captured_at, operator_label, images[], checks[] (check_key, verdict,
confidence, detail, model_version, latency_ms), outcome (decision,
decided_by, decided_at), overrides[], status, content_hash
```

**Verdicts:** `PASS` / `FAIL` / `UNCERTAIN`
**Decisions:** `SEAL` / `STOP_AND_FIX`

---

## Evaluation Results

### Decision Engine (80 cases, 0 false PASS)

| Dataset | Cases | Correct | False PASS | False STOP |
|---|---|---|---|---|
| Synthetic (8 scenarios) | 51 | 51 | **0** | 0 |
| Official CSV (`pack_sample.csv`) | 29 | 29 | **0** | 0 |
| **Total** | **80** | **80** | **0** | **0** |

### Findings from Official CSV

The CSV contains **2 deliberately wrong operator verdicts** (operator said "seal" for incorrect packages). Our engine independently detected both:

| Record | Issue | Operator | Engine |
|---|---|---|---|
| PCK-0034 | Extra USB-C cable in box | seal (wrong) | **STOP_AND_FIX** |
| PCK-0044 | Expected candle, got bottle | seal (wrong) | **STOP_AND_FIX** |

This proves the engine reasons independently from operator labels, as required.

### Per-Check Metrics (FP/FN separated per honesty rules)

| Check | TP | FP | FN | TN | UNCERTAIN |
|---|---|---|---|---|---|
| items_present | 14 | 0 | 0 | 10 | 0 |
| no_extra_items | 12 | 0 | 0 | 10 | 0 |
| quantity_match | 6 | 0 | 0 | 10 | 0 |

Full methodology and results: [`docs/EVALUATION.md`](docs/EVALUATION.md)

---

## Test Coverage

| Suite | Collected | Status |
|---|---|---|
| Decision engine | 21 | Passing in `python -m pytest tests -q` |
| Schema validation | 18 | Passing |
| Org isolation (Rule 1) | 6 | Passing |
| Image quality gate | 9 | Passing |
| Eval harness | 3 | Passing |
| Official CSV evaluation | 7 | Passing |
| Live VLM integration | 3 | Skipped when `GEMINI_API_KEY` is unset; they passed in the latest local run |
| VLM retry policy | 29 | Passing |
| Pending record and image path | 4 | Passing |
| **Total** | **100** | **100 passed** |

---

## Assumptions & limitations

### Assumptions
- The catalogue is optional. When it is supplied, the model is asked to match visible items to those SKUs and names. When it is empty, matching still uses the order lines.
- Photographs are of the open package, not of a sealed box.
- Seller catalogues in this build are small. Long-tail identification was not evaluated as a separate product.

### Limitations
- **Uncertain versus pending.** `pending_review` means the model ran and declined to judge (low confidence, similar products, or ambiguous image quality). The operational decision stored is `stop_and_fix` and the status is `pending_review`. The UI labels that **NEEDS REVIEW**, not a pass. `pending` means the model did not produce a verdict (timeout, API error after retries, or no usable photo). Nothing was checked. Those records have no `outcome` object, because inventing SEAL or STOP & FIX would be a fake decision.
- **Content hash.** `content_hash` is the SHA-256 of the evidence record's canonical JSON, excluding the hash field itself. It detects accidental change. It is not a tamper-proof, immutable, or anchored ledger.
- **Fail-open.** A Gemini error or timeout still saves the photographs and the order and still writes a `pending` record. The operator is not blocked. Transient failures (503, 429, 500, 504, network) are retried inside one inspection, still as a single logical call carrying every check. Auth and 400 errors are not retried.
- **FBA is out of scope.** The channel validator rejects `fba` and `amazon_fba`.
- **Vision depends on Gemini availability.** Without `GEMINI_API_KEY`, or when Gemini stays unavailable after the retry budget, the inspection is pending. This build does not include a second vision provider.
- **Incorrect items** are represented as a missing expected SKU plus an unexpected extra SKU. The engine does not emit a separate `wrong_item` discrepancy row. `no_wrong_items` passes unless that discrepancy type is present, which the current engine does not write.
- **Photos are served only to the owning organisation.** New files are stored under `storage/images/<org_id>/<unit_id>/`. The results page loads them from `GET /api/v1/inspections/{inspection_id}/images/{image_id}?org_id=`. The lookup is org-scoped, the image id must be on that record, and the path must stay inside the storage root. The storage directory is not mounted as public static files. Uploads are JPG, PNG, WebP, or GIF, and must stay under `max_image_size_mb`.
- **Counting.** Identical stacked items can be undercounted. That path is UNCERTAIN and human review, not a forced SEAL.
- **Storage.** SQLite plus local files. Queries are scoped by `org_id` in SQL. This is not Postgres row-level security.

### Known Failure Modes
1. Identical products stacked/overlapping → undercount → UNCERTAIN
2. Products still in opaque packaging → cannot identify → UNCERTAIN
3. Very poor image quality → all checks UNCERTAIN → human review
4. Product not in catalogue → detected as unknown extra item

---

## Project Structure

```
pack-manager/
├── app/
│   ├── api/routes.py          # FastAPI endpoints (verify, inspect, override, evidence)
│   ├── decision/engine.py     # Deterministic decision engine
│   ├── domain/schemas.py      # Pydantic data contracts
│   ├── vision/
│   │   ├── gemini_client.py   # Gemini VLM client (single batched call)
│   │   └── quality.py         # Image quality gate
│   ├── storage/database.py    # SQLite persistence (org-scoped)
│   ├── pipeline.py            # End-to-end orchestration
│   ├── config.py              # Environment settings
│   ├── main.py                # FastAPI app
│   └── templates/             # Operator UI
├── tests/
│   ├── unit/                  # Engine, schemas, org isolation, quality, VLM retry, pending path
│   ├── integration/           # Live VLM tests (need GEMINI_API_KEY)
│   └── evaluation/            # Eval harness (51 synthetic + 29 CSV = 80 cases)
├── submissions/yogesh-101/    # Submission deliverables
│   ├── 01-customer-letter.md
│   ├── 02-prfaq.md
│   ├── 03-one-pager.md
│   ├── CLAUDE.md
│   ├── build-brief.md
│   ├── build-log.md
│   ├── eval-report.md
│   └── contract/evidence-record.json
├── docs/
│   └── EVALUATION.md          # Eval report with metrics
├── data/                      # Organiser sample CSV (untouched)
├── ARCHITECTURE.md
├── .env.example
├── render.yaml                # One-click Render deployment
├── Dockerfile
├── docker-compose.yml
└── requirements.txt
```

---

## Technology Stack

| Component | Technology | Rationale |
|---|---|---|
| Backend | Python 3.13 + FastAPI | Best VLM ecosystem, typed schemas |
| Vision | Gemini Flash (structured output) | #1 on vision evals, free tier, native JSON schema |
| Schemas | Pydantic v2 | Validated contracts, JSON Schema generation |
| Storage | SQLite (WAL mode) | Zero-config, sufficient for individual build |
| UI | Jinja2 templates | Minimal, functional, no build step |
| Testing | pytest | `python -m pytest tests -q` (100 tests on the current tree) |

---

*CUBE Buildathon 2026 · Sydon.AI × CodeQuesters*
