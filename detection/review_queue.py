"""Human-review queue: payments the matcher refused to place.

The matcher only links a payment when it is confident. Everything else is
left unmatched on purpose. This script lists those payments, and for each one
shows the invoices a person should check first, so the reviewer does not
start from zero.

It never changes the database. It only reads and writes a CSV.

Run from the project root:  python -m detection.review_queue
"""
import csv
from decimal import Decimal

from sqlalchemy import select

from app.db import SessionLocal
from app.models import Customer, Invoice, Payment, PaymentAllocation
from detection.matcher import OVERPAY_CEIL, name_similarity

OUT_FILE = "docs/review_queue.csv"
LOOSE_FLOOR = Decimal("0.50")   # much wider than the matcher's 0.93: a human is deciding
NAME_MIN = 60                   # when the customer is unknown, the name must look plausible
TOP_N = 3                       # show the 3 closest candidate invoices per payment


def candidates_for(p, invoices, customers, paid_invoice_ids):
    """Unpaid invoices this payment could plausibly belong to, closest amount first."""
    found = []
    for inv in invoices:
        if inv.id in paid_invoice_ids:
            continue                                   # already has a payment
        if p.paid_date < inv.issue_date:
            continue                                   # paid before the invoice existed
        if p.customer_id is not None and inv.customer_id != p.customer_id:
            continue                                   # known customer: same customer only
        if p.customer_id is None and name_similarity(p, customers[inv.customer_id]) < NAME_MIN:
            continue                                   # unknown customer: name must be plausible
        ratio = p.amount / inv.amount
        if ratio < LOOSE_FLOOR or ratio > OVERPAY_CEIL:
            continue
        found.append((abs(1 - ratio), ratio, inv))
    found.sort(key=lambda row: row[0])
    return found[:TOP_N]


def main():
    with SessionLocal() as s:
        customers = {c.id: c.name for c in s.scalars(select(Customer))}
        invoices = s.scalars(select(Invoice)).all()
        paid_invoice_ids = set(s.scalars(select(PaymentAllocation.invoice_id)))

        unmatched = s.scalars(
            select(Payment)
            .outerjoin(PaymentAllocation, PaymentAllocation.payment_id == Payment.id)
            .where(PaymentAllocation.id.is_(None))
            .order_by(Payment.paid_date, Payment.id)
        ).all()

        rows = []
        for p in unmatched:
            cands = candidates_for(p, invoices, customers, paid_invoice_ids)
            print(f"\nPayment {p.id}: {p.bank_narration} | {p.amount} | {p.paid_date}")
            if not cands:
                print("   no plausible invoice found -> needs manual investigation")
                rows.append([p.id, p.bank_narration, p.amount, p.paid_date,
                             "", "", "", "", "no plausible invoice"])
                continue
            for _, ratio, inv in cands:
                days = (p.paid_date - inv.due_date).days
                print(f"   candidate {inv.invoice_no} ({customers[inv.customer_id]}) "
                      f"amount {inv.amount}  paid {ratio:.0%} of it, {days:+d} days vs due date")
                rows.append([p.id, p.bank_narration, p.amount, p.paid_date,
                             inv.invoice_no, customers[inv.customer_id], inv.amount,
                             f"{ratio:.2%}", "outside auto-match limits: human review"])

    with open(OUT_FILE, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["payment_id", "narration", "payment_amount", "paid_date",
                    "candidate_invoice", "customer", "invoice_amount",
                    "paid_fraction", "reason"])
        w.writerows(rows)

    print(f"\n{len(unmatched)} payments need human review. Saved to {OUT_FILE}")


if __name__ == "__main__":
    main()