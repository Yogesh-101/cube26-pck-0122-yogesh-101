# LinkedIn Post Draft

Not published. Replace the demo line, and the deployment line once Render is live, before posting. Then paste the live LinkedIn URL into the Round 2 form.

---

I built Pack Manager for Cube Buildathon Round 2, on the Pack Manager track.

A picker puts an order into a box. Before that box is sealed, someone still has to answer one question: does it contain exactly what the customer ordered? A wrong item or a wrong count becomes a refund, a return, and a replacement shipment. Pack Manager is the check for sellers and 3PLs who pack their own orders. Amazon FBA is out of scope, because Amazon packs those.

What I built

The operator photographs the open package, enters the order lines, and can attach a product catalogue. The agent looks for items present, missing items, the wrong item, the wrong quantity, and anything that should not be in the box. The operational result is SEAL or STOP & FIX.

How it works

The photo and the order go in together. One Gemini call observes every item and count. A deterministic decision engine, not the model, decides. If the photo is too ambiguous to judge, the verdict is UNCERTAIN and a person reviews it. It is not treated as a weak pass. Each run keeps an evidence record: expected versus observed quantities, per-check results, the photos, and a SHA-256 hash of the record so accidental change can be spotted. That hash is not a tamper-proof ledger. If an operator disagrees, the override is stored next to the original verdict, with their id and the reason. If the model is down, the capture is still saved as pending and the pack line is not blocked.

Technology / engineering

Python, FastAPI, and Pydantic. Vision is Gemini 2.5 Flash, one batched call per box rather than one call per check. Inspections are scoped to the organisation, so one tenant cannot open another tenant's record or photo. Transient model failures retry a bounded number of times, then fail open into a pending record.

Key challenge / learning

The dangerous failure is sealing a bad box. The model describes what it sees. The decision of whether that is good enough to close the box stays in code, and "I can't tell" is allowed.

Outcome

On 80 evaluation cases of that decision engine, given known observations rather than a live warehouse photo test, there were 0 false SEALs. The same run caught 2 operator verdicts in the sample data that would have sealed a bad package. Ambiguous photos were sent to review instead of forced into a decision.

Demo: [paste your video link]
GitHub: https://github.com/Yogesh-101/cube26-pck-0122-yogesh-101
Deployment: [paste your Render URL after https://pack-manager.onrender.com is live, or delete this line]

Building this was a chance to work on a real packing problem and to treat evaluation, evidence, and a failure mode as part of the product, not an afterthought.

Thank you to CodeQuesters and Sydon.AI.

@CodeQuesters @Sydon.AI

#CubeBuildathon #CUBE #SydonAI #CodeQuesters #AIBuilders #AIEngineering #AgenticAI #AIHackathon #BuildWithAI #AIInnovation #Hackathon2026 #BuildInPublic
