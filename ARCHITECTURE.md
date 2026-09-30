# Pack Manager — Architecture

## System architecture

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

## Data flow

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

## Components

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
- **One batched call per unit** (Engineering Rule 2). The payload is built once: prompt, order lines, catalogue, and every usable photo.
- Transient Gemini failures (HTTP 503, 429, 500, 504, timeouts, connection errors) are retried with bounded exponential backoff. `VLM_MAX_RETRIES` and `VLM_RETRY_BASE_SECONDS` come from settings. A 400 or 401 is not retried.
- A success returns immediately, so a retry is a repeat of that same single call, not one call per check.
- The model id sent to Gemini is `Settings.gemini_model`, default `gemini-2.5-flash` (`GEMINI_MODEL`).
- Receives: structured JSON via `response_schema`
- Returns: `ObservedItem[]` + quality assessment + notes
- On exhaustion or a non-retryable error: raises `VLMError`. The pipeline saves a `pending` inspection whose checks list carries the reason. The operator is not blocked.

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
- Every read and write is scoped to `org_id` (Engineering Rule 1)
- Tested: `org_demo_alpha` cannot see or override `org_demo_bravo` data, including by guessing an inspection id
- New photographs are stored under `storage/images/<org_id>/<unit_id>/`. Uploads must be JPG, PNG, WebP, or GIF and must stay under `max_image_size_mb`. Path segments reject `..` escapes.
- A photo is served only at `GET /api/v1/inspections/{inspection_id}/images/{image_id}?org_id=`. The inspection lookup is org-scoped, the image id must be listed on that record, and the file must resolve inside the storage root. There is no unauthenticated image URL.
- Override rows are append-only. `save_override` updates the `decision` column and leaves the stored `outcome` JSON as the agent's original verdict

### Pipeline Orchestration (`app/pipeline.py`)
- Coordinates: validate → quality gate → VLM → engine → evidence
- Fail-open on the quality gate: blur, darkness, overexposure, and duplicates are flagged `is_uncertain`, and those photos are still sent. The decision engine then treats image quality as uncertain rather than rejecting the inspection.
- Fail-open on VLM errors: saves a pending record, images, and a check whose `detail` is the error text
- If no image is usable, the same pending path runs and the vision client is not called

## Model / agent usage

The system is a **deterministic workflow with one Gemini call per unit**, not a multi-agent system.

| Stage | Technology | What it actually does |
|---|---|---|
| Product identification and counting | Gemini, model name from `GEMINI_MODEL` (default `gemini-2.5-flash`) | One `generate_content` request per unit. Prompt includes the order lines, the catalogue, and every usable photo. Structured JSON in, `ObservedItem` list out. |
| Retry | Same client | Only transient upstream failures. Budget is `VLM_MAX_RETRIES` plus the inspection timeout. |
| Image quality before the call | Pillow, and OpenCV when it is installed | Blur, brightness, size, duplicates. Unusable files are dropped from the batch. If none remain, no model call is made. |
| Decision | `app/decision/engine.py` | No model. Five named checks. Any FAIL → `stop_and_fix` / `completed`. No FAIL and any UNCERTAIN → `stop_and_fix` / `pending_review`. All PASS → `seal`. UNCERTAIN never becomes a pass. |
| Evidence hash | `hashlib.sha256` | Canonical JSON of the evidence record, excluding `content_hash`. |

A second model call per check was rejected because Engineering Rule 2 requires one batched call per unit. Extra agents were rejected for the same reason: at this catalogue scale they add failure modes without an accuracy gain. The decision engine is covered by unit tests that never call Gemini.

## Important engineering decisions

1. **Organisation isolation before features.** Inspection, list, override, and evidence queries all filter on `org_id`. The API returns 404 when the id belongs to another org. Image directories include the organisation id so two tenants that reuse a unit id do not share a folder.
2. **Fail-open.** A model error, timeout, or empty usable-photo set still persists the capture and a `pending` record. The reason is on `Inspection.checks` and on the evidence record. The results page shows that text and marks order lines **Not checked**.
3. **Uncertain is a verdict.** Per-check values are `pass`, `fail`, and `uncertain`. The UI maps `pending_review` to NEEDS REVIEW. It is not displayed as SEAL.
4. **Append-only overrides.** An override stores the original verdict, the new verdict, the operator id, and the reason. The `outcome` object written at inspection time is not replaced with the operator's decision. `GET /api/v1/inspections/{id}` adds `overrides` and `current_decision` without removing the original outcome.
5. **One batched call.** All checks are computed from one vision response. Retries resend that same request. They do not split work into one call per check.
6. **Honesty about the hash.** The content hash is an integrity fingerprint of JSON. The UI says it is not an anchored log. The code does not claim otherwise.

## Failure handling

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

- Docker: `Dockerfile` and `docker compose up --build`
- Local: `python -m uvicorn app.main:app --host 127.0.0.1 --port 8000` from the repo root
- Render: `render.yaml` declares a Docker web service named `pack-manager` on branch `main`. That file is not a live URL. A public URL exists only after the service is created in Render.
- Database: a SQLite file in the working directory. Queries filter on `org_id`. This is not Postgres row-level security.

---

*CUBE Buildathon 2026 · Sydon.AI × CodeQuesters*
