"""Quick analysis of the sample CSV data."""
from app.data_loader import load_sample_data

records = load_sample_data()
wrong = [r for r in records if not r["operator_correct"]]

print(f"Total rows: {len(records)}")
print(f"Correct orders: {sum(1 for r in records if r['discrepancy_type'] == 'correct')}")
print(f"Discrepant orders: {sum(1 for r in records if r['discrepancy_type'] != 'correct')}")
print(f"Deliberately wrong operator verdicts: {len(wrong)}")
for w in wrong:
    print(f"  {w['record_id']}: operator={w['operator_verdict']}, truth={w['ground_truth_decision']}, type={w['discrepancy_type']}")

print("\nDiscrepancy types:")
from collections import Counter
types = Counter(r["discrepancy_type"] for r in records)
for t, c in types.most_common():
    print(f"  {t}: {c}")
