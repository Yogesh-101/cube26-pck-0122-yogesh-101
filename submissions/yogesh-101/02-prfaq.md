# PR/FAQ — Pack Manager

## Press Release

**AI-Powered Pack Verification Agent Catches Mis-Ships Before the Box is Sealed**

*CUBE Buildathon 2026 — Pack Manager eliminates wrong-item shipments by verifying package contents from a single photograph*

Today we announce Pack Manager, an AI agent that verifies every order before it leaves the warehouse. From a photograph of an open package, Pack Manager identifies each item, counts quantities, and compares against the expected order — producing a SEAL or STOP & FIX decision in under 3 seconds.

Pack Manager uses a vision language model (Gemini Flash) for product identification and a deterministic rule engine for the final decision. The AI observes; the rules decide. This separation means the system never produces a confident wrong answer — when evidence is insufficient, it routes to human review.

In evaluation across 80 test cases including the official CUBE sample data, Pack Manager achieved **zero false SEAL decisions** — it never approved a package that should have been stopped. The system also caught 2 deliberately wrong operator verdicts in the sample data, proving it reasons independently.

Every inspection produces a structured evidence record with per-check verdicts, confidence scores, model metadata, and a content hash. These records integrate with Returns Manager and Recovery Manager via the shared `unit_id` join key.

---

## Frequently Asked Questions

### Customer Questions

**Q: What if the AI is wrong?**
A: When the AI cannot reach a confident conclusion, it produces an UNCERTAIN verdict and routes the package to a human. It never auto-seals an uncertain package. Human operators can override any AI decision, and both the original and overridden verdicts are preserved.

**Q: Does this slow down the packing line?**
A: No. If the model times out or errors, the system saves a pending record and lets the operator continue (fail-open). The photo capture adds ~5 seconds to the workflow; the AI responds in 1–3 seconds.

**Q: What about identical items — can it count 5 of the same thing?**
A: Counting stacked identical items is the hardest sub-problem for any vision system. Pack Manager handles this by using UNCERTAIN verdicts for low-confidence counts rather than guessing. We report this limitation honestly.

**Q: Can different warehouses see each other's data?**
A: No. Every query is scoped to the organization ID. We tested with two separate orgs — neither can see or access the other's inspections, images, or overrides.

**Q: What data do you keep?**
A: The evidence record, the photograph, the order context, and the decision. Human overrides are append-only — the original AI decision is never deleted.

### Questions We'd Rather Not Answer

**Q: What's the accuracy on real production images, not synthetic test data?**
A: Our 80-case evaluation covers the decision engine deterministically. Real-world VLM accuracy on production photographs in varied warehouse conditions has not been measured at scale. This is the honest gap between "tested" and "deployed."

**Q: Can it handle products still in their own packaging?**
A: Poorly. If a product is sealed in opaque packaging, the VLM cannot identify it. This produces an UNCERTAIN verdict, which is the correct behavior — but the operator still has to check manually.

**Q: What happens when the catalogue doesn't cover a product?**
A: The item is flagged as an unknown extra. This is correct for detecting wrong items, but means every new SKU addition needs a catalogue update.

**Q: Is the content hash legally sufficient as proof of what was shipped?**
A: No. It's a SHA-256 of the canonical JSON, providing integrity verification. It is not tamper-evident, notarized, or blockchain-anchored. We say what we built.
