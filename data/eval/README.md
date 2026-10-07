# Evaluation data

## Layout

```
data/eval/
├── catalogue.json              # Product catalogue used for SKU grounding
├── held_out/
│   ├── manifest.json           # Frozen ground truth (written BEFORE model runs)
│   ├── labels_labeller_a.json  # Primary ground truth
│   ├── labels_labeller_b.json  # Independent second pass (same author, disclosed)
│   └── images/*.jpg            # Open-box photo fixtures
└── results/
    ├── held_out_latest.json    # Latest machine-readable run (re-scorable)
    └── held_out_run_*.json     # Timestamped runs
```

## How to rebuild fixtures

```bash
python scripts/build_eval_photos.py
```

Downloads real product photographs (Unsplash), composites them into cardboard
open-box scenes, and freezes ground truth in `manifest.json`.

## How to run the held-out photo eval (order-blind Gemini)

```bash
python scripts/run_photo_eval.py --model gemini-flash-latest --sleep 20
# Resume after quota / 503s:
python scripts/run_photo_eval.py --model gemini-flash-latest --sleep 20 --resume
```

The VLM prompt receives **catalogue + photos only**. The expected order is
never sent to the model. The deterministic decision engine compares
observations to the order afterward.

## Honesty

- Rules-only decision-engine accuracy is reported separately and is **not**
  claimed as vision accuracy.
- Labeller B is an independent second pass by the same author — not a second
  person. That is disclosed in the manifest and eval report.
- Pending / timeout results are kept in the run file. Documenting what did
  not work counts as a useful outcome.
