# Round 3 integration

This Round 2 Pack Manager is integrated into the Pod monorepo as:

**https://github.com/Yogesh-101/cube-round3-pod/tree/main/agents/pack**

| | |
|---|---|
| Agent id | `pack-manager@2` |
| Adapter | `agents/pack/app.py` → `handle(agent_input) -> agent_output` |
| Runtime copy | `agents/pack/runtime/` (this repo) |
| Decision | Pod `docs/decisions.md` **D-007** |
| Held-out eval | 38/38 · **0 false SEAL** · 76.3% decision accuracy |
| Prompt | `order-blind-v2` |

### Production checklist (this repo)

- [x] Order-blind VLM prompt (expected order never sent to Gemini)
- [x] Deterministic decision engine; UNCERTAIN never auto-seals
- [x] Quality gate (blur + underlit) blocks false SEAL on ambiguous photos
- [x] Fail-open pending on VLM errors
- [x] Durable SQLite + JSONL mirror under `STORAGE_ROOT` (survives restart/redeploy)
- [x] OpenCV quality gate (blur / low light / contrast) — never auto-seals on bad photos
- [x] Tenant isolation on inspections
- [x] Honest A/B eval reporting (`docs/EVALUATION.md`)
- [x] **v2** Object-level bounding boxes + deciding_feature / alternative_skus
- [x] **v2** `scene_coverage` check (whole box visible / hidden items)
- [x] **v2** `photo_reuse` check (SHA-256 cross-order reuse, org-scoped)
- [x] **v2** UNCERTAIN → `pending_review` (never auto-seal)
- [x] **v2.1** Occlusion-aware missing (poor scene → UNCERTAIN, not false STOP)
- [x] **v2.1** `component_lookalikes` (included parts ≠ hard extra FAIL)

### Round 3 checklist (pod)

- [x] Contract-valid Agent Output for all pack sample units
- [x] Wrong-tenant → `LookupError` / HTTP 404
- [x] Idempotent `record_id` from `request_id`
- [x] FBA route fail-open (never seal)
- [x] Captures from `request.inputs` or `data/input/<id>/pack/`
- [x] `agent.json` describes the real implementation (not a stub)
