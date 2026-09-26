# One-Pager — Pack Manager

## Problem

Mis-ships cost sellers $15–25 per incident (return + refund + reship + review damage). At 2% error rate on 500 orders/day, that's $1,500–2,500/week in direct costs. Manual checking doesn't scale.

## Solution

AI agent that verifies package contents from a photograph before sealing.

```
Photo of open box → VLM identifies items → Decision engine → SEAL or STOP & FIX
```

## Metrics

| Metric | Value | Notes |
|---|---|---|
| Decision accuracy (eval set) | 100% (80/80) | 51 synthetic + 29 official CSV |
| **False SEAL (dangerous)** | **0** | Never approved a bad package |
| False STOP (safe side) | 0 | No unnecessary stops |
| Wrong operator verdicts caught | 2/2 | Engine independent of labels |
| Per-check FP | 0 | Separated per honesty rules |
| Per-check FN | 0 | Separated per honesty rules |
| VLM latency | 1–3s | Gemini Flash structured output |
| Tests passing | 64/64 | 6 suites |

## Architecture

| Layer | What | Why |
|---|---|---|
| Input | FastAPI + Pydantic v2 | Validated contracts in, structured data out |
| Vision | Gemini Flash (1 call/unit) | Rule 2: single batched call, not one per check |
| Decision | Deterministic engine | AI observes, code decides — no LLM in the loop |
| Evidence | CUBE contract + SHA-256 hash | Every verdict traceable to checks and images |
| Override | Append-only human flow | Original never overwritten (honesty rule) |

## Engineering Rules Compliance

| Rule | Implementation | Tested |
|---|---|---|
| 1. Tenancy isolation | org_id on every query, 6 isolation tests | Yes |
| 2. Batch model calls | Single call per unit | Yes |
| 3. Fail open | Pending record on error, operator unblocked | Yes |
| 4. UNCERTAIN first-class | Routes to review, never auto-seals | Yes |
| 5. Look up rules | Catalogue-grounded, no dummy data trusted | Yes |

## Kill Condition

**If the agent produces a single false SEAL on the evaluation set, the product is not safe to ship.**

Current status: 0 false SEALs across 80 cases.

## Known Limitations

1. Counting identical stacked items → UNCERTAIN (not wrong SEAL)
2. Opaque packaging → cannot identify → UNCERTAIN
3. Content hash is integrity verification, not tamper-evident
4. Decision engine eval only — real-image VLM eval requires fixture capture at scale

## Chain Position

```
Receiving → Prep → [PACK MANAGER] → Returns → Recovery
```

Output: evidence record with `unit_id` join key consumed by Returns and Recovery Managers.
