# Build Log — Pack Manager

## Timeline

### Day 1 — Sep 25, 2026

**M0: Foundation**
- Forked `cube26-pck-0122-yogesh-101`, cloned to local
- Read all PDFs: handbook, track brief, rules, evaluation criteria
- Analyzed `pack_sample.csv` (29 rows, 2 orgs, 10 SKUs, 2 deliberately wrong operator verdicts)
- Chose architecture: deterministic decision engine + single VLM call (not multi-agent)
- Implemented Pydantic v2 data contracts (`app/domain/schemas.py`)
  - Channel enum with FBA rejection validator
  - Verdict/Decision/CheckKey enums
  - EvidenceRecord matching official CUBE contract
- Built deterministic decision engine (`app/decision/engine.py`)
  - 5 named checks: IMAGE_QUALITY, ITEMS_PRESENT, QUANTITY_MATCH, NO_EXTRA_ITEMS, NO_WRONG_ITEMS
  - Decision logic: all PASS → SEAL, any FAIL → STOP_AND_FIX, UNCERTAIN → STOP_AND_FIX + pending_review
- 21 unit tests for decision engine covering all 8 official scenarios

**M1: Vision**
- Gemini VLM client with single batched structured-output call
- Prompt packer with order context + catalogue grounding
- Image quality gate (brightness, resolution, blur, SHA-256 dedup)
- 9 quality gate tests

**M2: Evidence & Persistence**
- SQLite with WAL mode, org-scoped queries throughout
- Human override flow (append-only, original preserved)
- Evidence record with content hash (SHA-256 of canonical JSON)
- 6 org isolation tests (alpha can't see bravo, can't guess IDs, override scoped)
- 12 schema validation tests

### Day 2 — Sep 26, 2026

**M3: Evaluation**
- Built eval harness with 51 synthetic cases across 8 scenarios
- Added 29-row CSV evaluation against official `pack_sample.csv`
- Discovered 2 deliberately wrong operator verdicts (PCK-0034, PCK-0044) — engine catches both
- Total: 80 cases, 0 false PASS, 0 false STOP
- 10 eval tests (3 harness + 7 CSV)

**M4: Hardening & Docs**
- Operator UI (Jinja2 templates): dashboard, new inspection, results with override
- FastAPI endpoints: verify, inspect, override, evidence export, health
- Evidence interop endpoints: `/evidence/by-unit/{unit_id}` for Round 3 cross-manager lookup
- Docker + docker-compose
- README, ARCHITECTURE.md, EVALUATION.md with full eval table
- Deployment config (render.yaml)

**M5: Submission**
- All submission deliverables: customer letter, PR/FAQ, one-pager, CLAUDE.md, build brief, build log, eval report, contract
- Live VLM end-to-end test
- Final test run: 64/64 passing

## Findings

1. **PCK-0034**: Order was candles + bottle, box had extra USB-C cable. Operator said "seal" (wrong). Engine correctly says STOP_AND_FIX.
2. **PCK-0044**: Order was candle, box had bottle instead. Operator said "seal" (wrong). Engine correctly says STOP_AND_FIX.
3. Both prove the engine is independent of operator labels — it reasons from expected vs observed data only.

## Decisions Made

| Decision | Alternatives Considered | Why This |
|---|---|---|
| Gemini Flash | GPT-5.6 Sol, Claude, Qwen-VL | #1 on vision benchmarks, free tier, native JSON schema |
| Deterministic engine | LLM-based decisions | Operational safety — AI observes, code decides |
| Single call | Multi-agent, per-check calls | Rule 2 mandate; cost at scale |
| UNCERTAIN first-class | Binary PASS/FAIL | Rule 4; better for ops than false confidence |
| SQLite | Postgres, DynamoDB | Zero-config for individual build; swappable |
| No per-SKU training | Fine-tuned classifiers | No training data; zero-shot VLM is sufficient |

## What I'd Do Next

1. Production Postgres with row-level security policies
2. Real image fixture dataset (50+ units, varied lighting)
3. Cohen's kappa with two independent labelers
4. Per-SKU accuracy breakdown on long-tail products
5. Integration test with Returns Manager evidence consumer

### Post Round-2 re-score remediation � Oct 6, 2026

Independent re-score dropped Pack 09 from 90 to 61 for: **rules-only 100% eval, no photos; order in prompt**.

- Made VLM **order-blind** (catalogue + images only; expected order never in prompt)
- Added held-out photo dataset (38 fixtures from real product photos) under `data/eval/held_out/`
- Added `scripts/build_eval_photos.py` and `scripts/run_photo_eval.py` (re-scorable, resumable)
- Fixed `no_wrong_items` substitution detection
- Split eval reporting: rules harness vs photo headline; honest free-tier quota pending documented
- 102 unit/evaluation tests passing
