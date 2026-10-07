# yogesh-101 · Pack Manager

**CUBE Buildathon 2026 · Track 03 · Pack Manager**
**Round 2 Individual Build — Yogesh Macherla**

## Links

| Resource | Link |
|---|---|
| Repository | [github.com/Yogesh-101/cube26-pck-0122-yogesh-101](https://github.com/Yogesh-101/cube26-pck-0122-yogesh-101) |
| Live Demo | _See deployment instructions in root README.md_ |
| Eval Report | [eval-report.md](eval-report.md) |
| Build Log | [build-log.md](build-log.md) |
| Evidence Contract | [contract/](contract/) |
| LinkedIn draft | [linkedin-post-draft.md](linkedin-post-draft.md) |

## Layout

```
submissions/yogesh-101/
├── README.md
├── 01-customer-letter.md
├── 02-prfaq.md
├── 03-one-pager.md
├── CLAUDE.md
├── build-brief.md
├── build-log.md
├── eval-report.md
├── linkedin-post-draft.md
└── contract/evidence-record.json
```

Agent code lives at the repo root (`app/`, `tests/`, etc.).

## Status

| Face | Deliverable | Status |
|---|---|---|
| 1 | Customer letter, PR/FAQ, one-pager | Done |
| 2 | CLAUDE.md | Done |
| 3 | Headless agent on fixtures | Done |
| 4 | Eval report | Done |
| 5 | Evidence record page | Done |
| 6 | Cross-pod contract | Done |

## Kill condition

If the agent produces a single false SEAL (approves a mis-shipped package) on the evaluation set, the product is not safe to ship.

## Evaluation notes

Order-blind VLM + held-out photo fixtures live under `data/eval/`. See [eval-report.md](eval-report.md).
