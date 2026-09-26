# Demo Video Recording Guide

Record a 2–3 minute screencast showing the full workflow. Use OBS Studio, Loom, or any screen recorder.

## Script

### 1. Dashboard (15s)
- Show the Pack Manager dashboard at `http://localhost:8000`
- Point out: agent name, version, "How It Works" steps

### 2. New Inspection — Correct Order (60s)
- Click "New Inspection"
- Fill in:
  - Order ID: `ORD-DEMO-001`
  - Unit ID: `UNIT-DEMO-001`
  - Org ID: `org_demo_alpha`
  - Channel: Shopify
  - Order Line: `SKU-CABLE-USBC`, Quantity: `1`
- Upload a photo of a USB-C cable in a box (take with your phone)
- Click "Verify Package"
- Show the result: **SEAL**, all 5 checks PASS, evidence record with content hash

### 3. New Inspection — Wrong Item (60s)
- Click "New Inspection" again
- Fill in:
  - Order ID: `ORD-DEMO-002`
  - Unit ID: `UNIT-DEMO-002`
  - Org ID: `org_demo_alpha`
  - Order Line: `SKU-BOTTLE-750`, Quantity: `1`
- Upload a photo of something that's NOT a water bottle
- Show the result: **STOP_AND_FIX**, the wrong-item/missing-item check FAILs

### 4. Human Override (30s)
- On the STOP result page, scroll to "Human Override"
- Enter: New Decision: SEAL, Reason: "Verified correct product, model error", Operator: "OP-001"
- Submit
- Show that original decision is preserved alongside the override

### 5. API Evidence Export (15s)
- Open a terminal
- Run: `curl http://localhost:8000/api/v1/inspections/INSPECTION-ID/evidence?org_id=org_demo_alpha`
- Show the structured evidence record JSON

### Tips
- Use a real phone photo of real products for maximum impact
- Keep the pace brisk — judges watch many demos
- Mention the key differentiators: "AI observes, code decides", "UNCERTAIN is first-class", "fail-open"
