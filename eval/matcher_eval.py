"""Grade the matcher: did each payment get matched to the invoice it truly belongs to?

Reads data_truth.csv (written by the generator) and compares it with the
payment_allocations table (written by the matcher).

Run from the project root:  python -m eval.matcher_eval
"""
import csv
import sys
from collections import defaultdict

from sqlalchemy import select

from app.db import SessionLocal
from app.models import PaymentAllocation

TRUTH_FILE = sys.argv[1] if len(sys.argv) > 1 else "data_truth.csv"


def load_truth():
    with open(TRUTH_FILE, newline="") as f:
        return {int(r["payment_id"]): int(r["invoice_id"]) for r in csv.DictReader(f)}


def main():
    truth = load_truth()

    with SessionLocal() as s:
        rows = s.execute(
            select(PaymentAllocation.payment_id,
                   PaymentAllocation.invoice_id,
                   PaymentAllocation.method)
        ).all()
    predicted = {pid: (inv_id, method) for pid, inv_id, method in rows}

    per_method = defaultdict(lambda: {"total": 0, "correct": 0})
    wrong = []
    unmatched = []

    for pid, true_inv in truth.items():
        if pid not in predicted:
            unmatched.append(pid)
            continue
        pred_inv, method = predicted[pid]
        per_method[method]["total"] += 1
        if pred_inv == true_inv:
            per_method[method]["correct"] += 1
        else:
            wrong.append((pid, pred_inv, true_inv, method))

    matched = sum(m["total"] for m in per_method.values())
    correct = sum(m["correct"] for m in per_method.values())

    print(f"{'method':30} {'matched':>8} {'correct':>8} {'accuracy':>9}")
    for method, m in sorted(per_method.items()):
        acc = m["correct"] / m["total"]
        print(f"{method:30} {m['total']:>8} {m['correct']:>8} {acc:>9.2%}")

    print()
    print("total payments:      ", len(truth))
    print("matched:             ", matched)
    print("left unmatched:      ", len(unmatched))
    print("matched correctly:   ", correct)
    if matched:
        print(f"accuracy when matched: {correct / matched:.2%}")
    print(f"overall correct:       {correct / len(truth):.2%}")

    if wrong:
        print("\nWRONG MATCHES (payment_id, matcher said invoice, truth was invoice, method):")
        for row in wrong:
            print("  ", row)
    if unmatched:
        print("\nUNMATCHED payment ids:", unmatched)


if __name__ == "__main__":
    main()