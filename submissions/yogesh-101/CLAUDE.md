# CLAUDE.md — Pack Manager Constraints

## Hard Rules

1. **Never SEAL uncertain evidence.** If any check is UNCERTAIN and no check is FAIL, the decision is STOP_AND_FIX with pending_review. Never auto-approve ambiguous packages.

2. **One model call per unit.** Engineering Rule 2 is non-negotiable. All checks are carried in a single batched VLM call. Never split into per-check calls.

3. **Fail open, always.** A model error or timeout saves a pending record. Nothing blocks the operator. Nothing makes the warehouse line wait.

4. **Org isolation on every query.** Every database query, every API response, every image path is scoped to `org_id`. No exceptions, no shortcuts.

5. **Overrides are append-only.** When an operator overrides, the original AI decision is preserved alongside the new decision and reason. Never delete, mutate, or hide the original verdict.

6. **No secrets in the repo.** API keys, tokens, passwords, `.env` files are never committed. `.env.example` uses placeholders only.

7. **Content hash is not tamper-evident.** SHA-256 of canonical JSON is integrity verification. Do not claim immutability, legal proof, or blockchain-level guarantees.

8. **Report honest numbers.** Per-check FP and FN separated. Never aggregate into a single accuracy number without the breakdown. "It works well" is not a result.

## Forbidden Language

- "tamper-proof" / "tamper-evident" / "immutable record" (unless actually built)
- "blockchain" / "anchored" (not implemented)
- "100% accurate" (no system is)
- "replaces human operators" (it assists them)
- "guaranteed" (nothing is)
- "AI decides" (AI observes, deterministic code decides)

## Conventions

- Python 3.13, FastAPI, Pydantic v2
- All domain types in `app/domain/schemas.py`
- Decision logic in `app/decision/engine.py` — no LLM calls inside
- VLM client in `app/vision/gemini_client.py` — structured JSON output only
- Tests in `tests/` with pytest — run `python -m pytest tests/ -v`
- Evidence records follow the official CUBE contract schema

## Data Rules

- Sample CSV data (`data/pack_sample.csv`) is reference data with dummy requirement flags
- Engineering Rule 5: look authoritative rules up, don't infer from examples
- Two orgs in sample data (`org_demo_alpha`, `org_demo_bravo`) exist for isolation testing
- Operator verdicts in CSV are sometimes deliberately wrong — engine must be independent
