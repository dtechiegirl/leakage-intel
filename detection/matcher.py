import re
from collections import Counter, defaultdict
from decimal import Decimal

from sqlalchemy import delete, select

from app.db import SessionLocal
from app.models import Customer, Invoice, Payment, PaymentAllocation
from rapidfuzz import fuzz

DIGITS = re.compile(r"\d+")

PARTIAL_FLOOR = Decimal("0.93")   # payment may be up to 7% short of the invoice
REF_FLOOR = Decimal("0.80")
OVERPAY_CEIL = Decimal("1.001")   # but not more than rounding above it
EARLY_DAYS = 10                   # allowed to pay this many days before the due date
LATE_DAYS = 120                   # and this many days after it
DUP_WINDOW_DAYS = 7               # same amount within a week = likely a repeat payment
EXACT_TOLERANCE = Decimal("0.01") # amounts within 1 kobo count as "exactly equal"
NAME_MIN = 60                     # name must be at least this similar to be plausible
TIE_GAP = 5                       # if top two names are this close, we can't decide


def invoice_number(reference):
    """'INV-00231', 'INV00231', '231', 'INV-00231 PAYMENT' all become 231."""
    if not reference:
        return None
    m = DIGITS.search(reference)
    return int(m.group()) if m else None


def name_similarity(p, customer_name):
    """How much does the name in the bank narration look like this customer? (0-100)"""
    text = (p.bank_narration or "").upper().replace("TRF FROM ", "")
    name = text.split(" / ")[0].strip()
    if not name:
        return 0
    return fuzz.WRatio(name, customer_name.upper())

def amount_fits(p, inv):
    """Is the payment amount plausible for this invoice?"""
    return REF_FLOOR <= p.amount / inv.amount <= OVERPAY_CEIL


def exact_match_elsewhere(p, inv, invoices, by_customer, allocated):
    """The reference points at `inv`, but if the amount isn't exact for it
    and IS exact for another unpaid invoice, the reference is probably a typo."""
    if abs(p.amount - inv.amount) <= EXACT_TOLERANCE:
        return False
    pool = by_customer.get(p.customer_id, []) if p.customer_id is not None else invoices
    for other in pool:
        if (other.id != inv.id and other.id not in allocated
                and abs(p.amount - other.amount) <= EXACT_TOLERANCE):
            return True
    return False

def find_by_amount(p, candidates, allocated):
    """Pass 2, customer KNOWN: use that customer's invoices + amount + dates."""
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
        # after that, amount gap and timing decide.
        is_exact = abs(p.amount - inv.amount) <= EXACT_TOLERANCE
        score = (0 if is_exact else 1,
                 float(abs(1 - ratio)) + abs(days_from_due) / 365)
        if best_score is None or score < best_score:
            best, best_score = inv, score
    if best is not None:
        return best, "amount_date", Decimal("0.60")
    return None, None, None


def find_global(p, invoices, allocated, cust_names):
    """Pass 2, customer UNKNOWN: the amount leads, the name only breaks ties.

    Returns (None, None, None) when unsure. Better to leave a payment for a
    human than to match it wrongly and hide a real leak.
    """
    # (a) Is this a repeat of a payment we've already matched?
    dup_hits = []
    for inv in invoices:
        for other in allocated.get(inv.id, []):
            same_amount = other.amount == p.amount
            close_in_time = abs((p.paid_date - other.paid_date).days) <= DUP_WINDOW_DAYS
            if same_amount and close_in_time:
                sim = name_similarity(p, cust_names[inv.customer_id])
                if sim >= NAME_MIN:
                    dup_hits.append((sim, inv))
    if dup_hits:
        dup_hits.sort(key=lambda h: h[0], reverse=True)
        return dup_hits[0][1], "fuzzy_duplicate_of_existing", Decimal("0.70")

    # (b) Look at EVERY unpaid invoice, whoever the customer is.
    scored = []   # (is_exact, name_score, -days_from_due, invoice)
    for inv in invoices:
        if inv.id in allocated:
            continue
        if p.paid_date < inv.issue_date:
            continue
        ratio = p.amount / inv.amount
        if ratio < PARTIAL_FLOOR or ratio > OVERPAY_CEIL:
            continue
        days_from_due = (p.paid_date - inv.due_date).days
        if days_from_due < -EARLY_DAYS or days_from_due > LATE_DAYS:
            continue
        is_exact = abs(p.amount - inv.amount) <= EXACT_TOLERANCE
        sim = name_similarity(p, cust_names[inv.customer_id])
        scored.append((is_exact, sim, -abs(days_from_due), inv))

    # Exactly one invoice has this exact amount -> that's the one.
    exact_hits = [s for s in scored if s[0]]
    if len(exact_hits) == 1:
        return exact_hits[0][3], "fuzzy_exact_amount", Decimal("0.85")

    # Otherwise the name must be at least plausible, then pick the best.
    pool = exact_hits or scored
    pool = [s for s in pool if s[1] >= NAME_MIN]
    if not pool:
        return None, None, None
    pool.sort(key=lambda s: (s[1], s[2]), reverse=True)

    # Top two look equally good -> don't guess, leave it for human review.
    if len(pool) > 1 and pool[0][1] - pool[1][1] < TIE_GAP:
        return None, None, None
    return pool[0][3], "fuzzy_amount_date", Decimal("0.60")


def run_matching():
    with SessionLocal() as s:
        s.execute(delete(PaymentAllocation))   # safe to re-run
        invoices = s.scalars(select(Invoice)).all()
        customers = s.scalars(select(Customer)).all()
        cust_names = {c.id: c.name for c in customers}
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
        unmatched = 0

        # Pass 1: the reference names an invoice, and the customer agrees.
                # Pass 1: the reference names an invoice, the amount fits, and
        # no other invoice matches the amount better.
        for p in payments:
            n = invoice_number(p.reference)
            inv = by_number.get(n) if n is not None else None
            trusted = (inv is not None
                       and amount_fits(p, inv)
                       and not exact_match_elsewhere(p, inv, invoices, by_customer, allocated))
            if trusted and inv.customer_id == p.customer_id:
                allocated.setdefault(inv.id, []).append(p)
                results.append((p, inv, "reference", Decimal("0.95")))
            elif trusted and p.customer_id is None:
                allocated.setdefault(inv.id, []).append(p)
                results.append((p, inv, "reference_no_customer", Decimal("0.85")))
            else:
                leftovers.append(p)

        # Pass 2: everything Pass 1 could not place.
        for p in leftovers:
            if p.customer_id is not None:
                inv, method, conf = find_by_amount(
                    p, by_customer.get(p.customer_id, []), allocated)
            else:
                inv, method, conf = find_global(p, invoices, allocated, cust_names)

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