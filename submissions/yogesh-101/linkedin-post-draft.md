# LinkedIn Post Draft

---

**Built an AI agent that catches mis-ships before the box is sealed.**

For the CUBE Buildathon 2026 (Sydon.AI × CodeQuesters), I built **Pack Manager** — an AI packing verification agent for warehouse operations.

The problem: When a picker assembles an order, sometimes the wrong item or quantity goes into the box. At scale, even a 2% error rate means thousands of dollars in returns and refunds every week.

**What Pack Manager does:**
- Takes a photo of the open package
- Identifies every item and counts quantities using Gemini Flash VLM
- Compares against the expected order
- Returns SEAL (ship it) or STOP & FIX (something's wrong)
- Produces a timestamped evidence record for dispute resolution

**Key design decisions:**
- AI observes, deterministic code decides — the final SEAL/STOP never comes from raw LLM output
- UNCERTAIN is a first-class verdict — the system says "I don't know" rather than guessing
- Fail-open — if the AI goes down, operators keep working
- Every decision is traceable to per-check verdicts, confidence scores, and photos

**Results:** 80 evaluation cases, 0 false SEALs (never approved a bad package), caught 2 deliberately wrong operator verdicts in the sample data.

Track 03 · Pack Manager · Round 2 Individual Build

#CUBEBuildathon #AI #Agents #WarehouseOps #VisionAI #BuildInPublic

---

*Copy-paste this to LinkedIn. Edit the tone to match your voice. Add a screenshot of the dashboard UI.*
