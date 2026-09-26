# Build Brief — Pack Manager

## What am I building?

An AI-powered packing verification agent that photographs open packages before sealing and determines whether the contents match the expected order.

## Who is it for?

Warehouse operators at merchant-fulfilled and 3PL facilities who pack 100–1000+ orders per day and need to catch mis-ships before they leave the building.

## What's the core loop?

```
Operator opens box → Takes photo → Agent verifies → SEAL or STOP & FIX
```

## What does success look like?

- Zero false SEALs (never approves a bad package)
- UNCERTAIN routes to humans (never forces a conclusion)
- Sub-3-second response (doesn't slow the line)
- Evidence record on every unit (disputes have receipts)

## What are the constraints?

1. Single batched VLM call per unit (cost at scale)
2. Tenancy isolation (multi-org from day one)
3. Fail-open (never block the warehouse)
4. No FBA orders (Amazon packs those)
5. Evidence contract compatible with Returns + Recovery Managers

## Technology choices

| Choice | Why |
|---|---|
| Gemini Flash | #1 on vision evals, free tier, native structured output |
| Deterministic decision engine | AI observes, code decides — no LLM jitter on operations |
| Pydantic v2 | Validated contracts, not vibes |
| SQLite WAL | Zero-config for individual build; Postgres for production |
| Single call, not multi-agent | Rule 2 forbids per-check calls; multi-agent adds latency + cost |

## What I'm NOT building

- Per-SKU trained classifiers (no training data available)
- Blockchain-anchored audit trail (overclaim per honesty rules)
- Multi-agent orchestration (violates Rule 2, adds unnecessary complexity)
- FBA verification (Amazon's responsibility, explicitly out of scope)
