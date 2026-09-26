# Pack Manager — AI Packing Verification Agent

**CUBE Buildathon 2026 · Track 03 · Pack Manager**
**Round 2 Individual Build — Yogesh Macherla**

> Verify every order before the box is sealed. From a photograph of the open package, determine: does this box contain exactly what the customer ordered?

---

## Problem

A picker assembles an order and closes the box. If the wrong item or quantity goes in, the customer gets a mis-ship: a refund, a return, a replacement shipment, and a bad review. Manual checking doesn't scale.

**Pack Manager** is an AI-powered agent that:
- Analyzes photographs of an open package
- Identifies every item and counts quantities
- Compares against the expected order
- Produces a decision: **SEAL** or **STOP & FIX**
- Leaves structured evidence for every decision

### Scope

This agent serves **merchant-fulfilled and 3PL orders only**. FBA orders are packed by Amazon and are explicitly out of scope.

---

## Solution Overview

```
Order + Catalogue → Input Validation → Image Quality Gate → Gemini VLM (single batched call)
    → Schema Validation → Decision Engine → Evidence Record → Operator UI
```

**Architecture principle**: AI provides observations; deterministic code provides control. The VLM identifies products and counts items. A rule engine makes the final operational decision. UNCERTAIN is a first-class outcome — the system never forces a conclusion from ambiguous evidence.

### Key Design Decisions

| Decision | Rationale |
|---|---|
| Single batched VLM call per unit | Engineering Rule 2: one call carrying all checks, not one per check |
| Deterministic decision engine | Final SEAL/STOP never depends on raw LLM output |
| UNCERTAIN → human review | Never auto-seal when evidence is insufficient |
| Fail-open on errors | Engineering Rule 3: model timeout saves pending record, never blocks operator |
| Org-scoped queries | Engineering Rule 1: tenancy isolation tested with two demo orgs |
| Content hash on evidence | SHA-256 of canonical JSON for integrity verification |

---

## Setup

### Prerequisites

- Python 3.11+
- Gemini API key ([get one free](https://aistudio.google.com/apikey))

### Installation

```bash
git clone https://github.com/Yogesh-101/cube26-pck-0122-yogesh-101.git
cd cube26-pck-0122-yogesh-101

python -m pip install -r requirements.txt

cp .env.example .env
# Edit .env and add your GEMINI_API_KEY
```

### Run

```bash
python -m uvicorn app.main:app --reload --port 8000
```

Open http://localhost:8000 in your browser.

### Run Tests

```bash
python -m pytest tests/ -v
```

### Run Evaluation

```bash
python -m tests.evaluation.eval_harness --output docs/EVALUATION.md
```

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

Get full inspection detail with evidence record.

### `POST /api/v1/inspections/{id}/override?org_id=...`

Human override — preserves original AI decision.

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

| Metric | Value |
|---|---|
| Total eval cases | 51 |
| Decision accuracy | **100%** |
| **False PASS (dangerous)** | **0** |
| False STOP | 0 |
| UNCERTAIN → human review | 7 |
| Per-check FP | 0 |
| Per-check FN | 0 |

*Decision engine evaluation on deterministic test data. VLM-stage evaluation with held-out image data documented separately in `docs/EVALUATION.md`.*

---

## Test Coverage

| Suite | Tests | Status |
|---|---|---|
| Decision engine | 21 | All pass |
| Schema validation | 12 | All pass |
| Org isolation | 6 | All pass |
| Image quality gate | 9 | All pass |
| Eval harness | 3 | All pass |
| **Total** | **57** | **All pass** |

---

## Assumptions & Limitations

### Assumptions
- Product catalogue is provided per order (catalogue-grounded identification)
- Images are captured from a phone camera of a reasonably lit open box
- Seller catalogues are small (20–60 SKUs) — long-tail identification is the known hard problem

### Limitations
- **VLM counting reliability**: Counting identical stacked items in a box is an unsolved problem for current VLMs. The system mitigates this with UNCERTAIN verdicts and human review.
- **No per-SKU training**: The system uses zero-shot VLM identification. For visually similar products (e.g., same shirt in two colors), accuracy depends heavily on image quality.
- **Single-session**: Current deployment uses SQLite and in-memory state; production would need Postgres with proper RLS.

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
│   ├── api/routes.py          # FastAPI endpoints
│   ├── decision/engine.py     # Deterministic decision engine
│   ├── domain/schemas.py      # Pydantic data contracts
│   ├── vision/
│   │   ├── gemini_client.py   # Gemini VLM client
│   │   └── quality.py         # Image quality gate
│   ├── storage/database.py    # SQLite persistence
│   ├── pipeline.py            # End-to-end orchestration
│   ├── config.py              # Environment settings
│   ├── main.py                # FastAPI app
│   └── templates/             # Operator UI
├── tests/
│   ├── unit/                  # 48 unit tests
│   └── evaluation/            # Eval harness + 51 cases
├── docs/
│   └── EVALUATION.md          # Eval report with metrics
├── data/                      # Organiser sample CSV (untouched)
├── ARCHITECTURE.md
├── .env.example
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
| Testing | pytest | 57 tests across 5 suites |

---

*CUBE Buildathon 2026 · Sydon.AI × CodeQuesters*
