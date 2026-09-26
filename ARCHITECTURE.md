# Pack Manager — Architecture

## System Architecture

```
                    ┌─────────────────────────────────────────────────┐
                    │                  Operator UI                     │
                    │  (New Inspection / Results / Override / Dashboard)│
                    └─────────────┬───────────────────────────────────┘
                                  │ HTTP
                    ┌─────────────▼───────────────────────────────────┐
                    │              FastAPI Application                  │
                    │  /api/v1/verify  /inspections  /override /health │
                    └─────────────┬───────────────────────────────────┘
                                  │
                    ┌─────────────▼───────────────────────────────────┐
                    │           Inspection Pipeline                     │
                    │                                                  │
                    │  1. Input Validation (Pydantic)                  │
                    │  2. Image Quality Gate (blur/brightness/dup)     │
                    │  3. VLM Call (single batched Gemini request)     │
                    │  4. Schema + Semantic Validation                 │
                    │  5. Deterministic Decision Engine                │
                    │  6. Evidence Record + Content Hash               │
                    └──────┬──────────────┬───────────────────────────┘
                           │              │
              ┌────────────▼──┐    ┌──────▼─────────┐
              │  Gemini VLM   │    │  SQLite + Files │
              │  (External)   │    │  (Persistence)  │
              └───────────────┘    └────────────────┘
```

## Data Flow

```
Order JSON ──┐
             ├──▶ Input Validation ──▶ Prompt Packer ──▶ Gemini VLM ──┐
Images ──────┘     (Pydantic)          (order+catalogue    (single     │
  │                  │                  +images → prompt)    call)      │
  │                  │                                                  │
  ▼                  │ reject FBA,                                      ▼
Image Quality        │ bad data       ┌── Schema Validation ◄──────────┘
Gate ────────────────┘                │   (parse structured JSON)
  │                                   │
  │ flag: blur,                       │ malformed? ──▶ PENDING (fail-open)
  │ dark, dup                         │
  │                                   ▼
  └──────────────────────▶ Decision Engine (deterministic)
                                      │
                          ┌───────────┼───────────┐
                          ▼           ▼           ▼
                        SEAL    STOP_AND_FIX  UNCERTAIN
                          │           │        (→ human review)
                          └─────┬─────┘           │
                                ▼                 ▼
                         Evidence Record    Operator Override
                         (official contract)  (preserves original)
                                │
                                ▼
                          SQLite + Audit
```

## Component Responsibilities

### Input Validation (`app/domain/schemas.py`)
- Enforces typed contracts via Pydantic v2
- Rejects FBA channels (out of scope)
- Validates quantities > 0, non-empty SKUs
- Generates UUIDs for inspection/image IDs

### Image Quality Gate (`app/vision/quality.py`)
- Deterministic checks: file existence, size, resolution
- Brightness assessment (too dark / overexposed)
- Optional blur detection (OpenCV Laplacian variance)
- SHA-256 for deduplication across batch
- Flags issues as `is_uncertain` rather than rejecting — fail-open

### Gemini VLM Client (`app/vision/gemini_client.py`)
- **One batched call per unit** (Engineering Rule 2)
- Sends: system prompt + order context + catalogue + all images
- Receives: structured JSON via `response_schema`
- Returns: `ObservedItem[]` + quality assessment + notes
- On failure: raises `VLMError` → caller does fail-open

### Decision Engine (`app/decision/engine.py`)
- **Deterministic** — no AI in the decision path
- 5 named checks: `items_present`, `quantity_match`, `no_extra_items`, `no_wrong_items`, `image_quality`
- Each check: `PASS` / `FAIL` / `UNCERTAIN`
- Final logic:
  - All PASS → `SEAL`
  - Any FAIL → `STOP_AND_FIX`
  - No FAIL + any UNCERTAIN → `STOP_AND_FIX` with `pending_review` status
  - FAIL overrides UNCERTAIN (no hidden failures)
  - UNCERTAIN never auto-seals

### Evidence Contract (`app/domain/schemas.py` → `EvidenceRecord`)
- Matches the official CUBE evidence contract
- Fields: `record_id`, `schema_version`, `organization_id`, `checks[]`, `outcome`, `overrides[]`, `status`, `content_hash`
- `content_hash`: SHA-256 of canonical JSON (excluding hash itself)
- Interoperable with Returns Manager and Recovery Manager

### Storage (`app/storage/database.py`)
- SQLite with WAL mode for read concurrency
- Every query scoped to `org_id` (Engineering Rule 1)
- Tested: org_demo_alpha cannot see org_demo_bravo data
- Override records append-only — original AI decision never overwritten

### Pipeline Orchestration (`app/pipeline.py`)
- Coordinates: validate → quality gate → VLM → engine → evidence
- Fail-open on VLM errors: saves pending record with captured images
- Fail-open on quality gate: flags uncertain, still processes

## Model Usage

| Stage | Technology | Rationale |
|---|---|---|
| Product identification | Gemini Flash VLM | #1 vision evals, 99% identification, structured output |
| Quantity counting | Gemini Flash VLM | 80%+ counting accuracy, single call |
| Image quality (basic) | Pillow + OpenCV | Deterministic, no model cost |
| Decision logic | Python rule engine | Deterministic, testable, auditable |
| Evidence hashing | hashlib SHA-256 | Standard, deterministic |

## Agent Workflow

The system is a **deterministic workflow with a single VLM call**, not a multi-agent system. This was a deliberate architectural choice:

- Multi-agent would violate Engineering Rule 2 (batch model calls)
- Additional agents add failure modes without accuracy gains at this catalogue scale
- The deterministic decision engine is fully testable without the VLM

## Failure Handling

| Failure | Behavior | Record |
|---|---|---|
| VLM timeout | Pending record saved, operator unblocked | `status: pending` |
| VLM malformed output | Pending record saved | `status: pending` |
| VLM JSON parse error | Pending record saved | `status: pending` |
| Image too large | Flagged, skipped from batch | Quality result logged |
| Image corrupt | Flagged, skipped from batch | Quality result logged |
| All images unusable | Pending record with no VLM call | `status: pending` |
| DB write failure | In-memory fallback, logged | Warning logged |

## Security

- API keys never in code — environment variables only
- `.env.example` provided, `.env` in `.gitignore`
- All file uploads validated (size, type, resolution)
- Org isolation on every query (tested with two orgs)
- No path traversal — uploads go to controlled directory
- Model output validated against schema before decision engine

## Observability

- Structured Python logging (timestamp, level, module)
- Per-check `latency_ms` and `model_version` in evidence
- `content_hash` for evidence integrity
- Override audit trail (original + new + reason + operator + timestamp)

## Deployment

- Docker: `Dockerfile` + `docker-compose.yml`
- Local: `uvicorn app.main:app --port 8000`
- Cloud: Render / Railway / any container host
- DB: SQLite file (production would use Postgres with RLS)

---

*CUBE Buildathon 2026 · Sydon.AI × CodeQuesters*
