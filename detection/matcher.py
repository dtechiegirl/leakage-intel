import re
from collections import Counter, defaultdict
from decimal import Decimal

from sqlalchemy import delete, select

from app.db import SessionLocal
from app.models import Invoice, Payment, PaymentAllocation

DIGITS = re.compile(r"\d+")

PARTIAL_FLOOR = Decimal("0.93")   # payment may be up to 7% short of the invoice
OVERPAY_CEIL = Decimal("1.001")   # but not more than rounding above it
EARLY_DAYS = 10                   # allowed to pay this many days before the due date
LATE_DAYS = 120                   # and this many days after it
DUP_WINDOW_DAYS = 7               # same amount within a week = likely a repeat payment


def invoice_number(reference):
    """'INV-00231', 'INV00231', '231', 'INV-00231 PAYMENT' all become 231."""
    if not referene:c
        return None
    m = DIGITS.search(reference)
    return int(m.group()) if m else None


def find_by_amount(p, candidates, allocated):
    """Pass 2: no usable reference, so use customer + amount + dates."""
    # (a) Is this a repeat of a payment we've already matched?
    for inv in candidates:
        for other in allocated.get(inv.id, []):
            same_amount = other.amount == p.amount
            close_in_time = abs((p.paid_date - other.paid_date).days) <= DUP_WINDOW_DAYS
            if same_amount and close_in_time:
                return inv, "duplicate_of_existing", Decimal("0.70")

    # (b) Otherwise find the best still-unpaid invoice for this customer.
    best, best_score = None, None
    for inv in candidates:
        if inv.id in allocated:
            continue
        ratio = p.amount / inv.amount
        if ratio < PARTIAL_FLOOR or ratio > OVERPAY_CEIL:
            continue
        days_from_due = (p.paid_date - inv.due_date).days
        if days_from_due < -EARLY_DAYS or days_from_due > LATE_DAYS:
            continue
        if p.paid_date < inv.issue_date:
            continue
        # Lower score = better. Amount gap counts more than timing.
        score = float(abs(1 - ratio)) + abs(days_from_due) / 365
        if best_score is None or score < best_score:
            best, best_score = inv, score
    if best is not None:
        return best, "amount_date", Decimal("0.60")
    return None, None, None


def run_matching():
    with SessionLocal() as s:
        s.execute(delete(PaymentAllocation))   # safe to re-run

        invoices = s.scalars(select(Invoice)).all()
        payments = s.scalars(
            select(Payment).order_by(Payment.paid_date, Payment.id)
        ).all()

        by_number = {int(i.invoice_no.split("-")[1]): i for i in invoices}
        by_customer = defaultdict(list)
        for inv in invoices:
            by_customer[inv.customer_id].append(inv)

        allocated = {}      # invoice_id -> payments already matched to it
        results = []        # (payment, invoice, method, confidence)
        leftovers = []

        # Pass 1: the reference names an invoice, and the customer agrees.
        for p in payments:
            n = invoice_number(p.reference)
            inv = by_number.get(n) if n is not None else None
            if inv is not None and inv.customer_id == p.customer_id:
                allocated.setdefault(inv.id, []).append(p)
                results.append((p, inv, "reference", Decimal("0.95")))
            else:
                leftovers.append(p)

        # Pass 2: everything Pass 1 could not place.
        unmatched = 0
        for p in leftovers:
            inv, method, conf = find_by_amount(p, by_customer.get(p.customer_id, []), allocated)
            if inv is None:
                unmatched += 1
                continue
            allocated.setdefault(inv.id, []).append(p)
            results.append((p, inv, method, conf))

        s.add_all([
            PaymentAllocation(payment_id=p.id, invoice_id=inv.id,
                              method=method, confidence=conf)
            for p, inv, method, conf in results
        ])
        s.commit()

        print("payments:", len(payments))
        print("by method:", dict(Counter(m for _, _, m, _ in results)))
        print("unmatched payments:", unmatched)
        print("invoices with at least one payment:", len(allocated))


if __name__ == "__main__":
    run_matching()