import random
from collections import Counter
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import delete, select, func

from app.db import SessionLocal
from app.models import BankTransaction, Customer, Expense, Invoice, Payment, PlantedLeak

SEED = 42
AS_OF = date(2026, 10, 31)       # the "today" of the fake company's books
N_CUSTOMERS = 20
INVOICES_PER_CUSTOMER = 25
N_UNPAID = 10
N_PARTIAL = 8
N_DUPLICATE = 8

SURNAMES = ["Adebayo", "Okafor", "Balogun", "Eze", "Bello", "Nwosu", "Ibrahim",
            "Olawale", "Chukwu", "Musa", "Adeyemi", "Obi", "Yusuf", "Danjuma",
            "Okonkwo", "Lawal", "Afolabi", "Umeh", "Salami", "Ogunleye", "Hassan"]
SUFFIXES = ["Enterprises", "Stores", "Logistics", "Pharmacy", "Ventures", "Foods", "Motors"]
BRANCHES = ["Lagos", "Abuja", "Port Harcourt", "Kano"]
INDUSTRIES = ["Retail", "Logistics", "Healthcare", "Manufacturing"]


def money(x) -> Decimal:
    """Exact 2-decimal money. Going through str avoids float noise."""
    return Decimal(str(x)).quantize(Decimal("0.01"))


def wipe(s):
    # Children before parents, because of foreign keys.
    for model in (PlantedLeak, BankTransaction, Payment, Expense, Invoice, Customer):
        s.execute(delete(model))


def build_customers(rng):
    customers, used = [], set()
    while len(customers) < N_CUSTOMERS:
        name = f"{rng.choice(SURNAMES)} {rng.choice(SUFFIXES)}"
        if name in used:
            continue
        used.add(name)
        customers.append(Customer(
            name=name,
            branch=rng.choice(BRANCHES),
            industry=rng.choice(INDUSTRIES),
            payment_terms_days=rng.choice([14, 30, 30, 45, 60]),
        ))
    return customers


def build_invoices(rng, customers):
    rows = []
    for c in customers:
        typical = rng.uniform(50_000, 800_000)   # this customer's usual invoice size
        for _ in range(INVOICES_PER_CUSTOMER):
            issue = AS_OF - timedelta(days=rng.randint(75, 365))
            rows.append((issue, c, money(typical * rng.uniform(0.7, 1.3))))
    rows.sort(key=lambda r: r[0])                # number invoices in date order
    return [
        Invoice(customer_id=c.id, invoice_no=f"INV-{i:05d}", amount=amt,
                issue_date=issue, due_date=issue + timedelta(days=c.payment_terms_days))
        for i, (issue, c, amt) in enumerate(rows, start=1)
    ]


def messy_reference(rng, invoice_no):
    digits = invoice_no.split("-")[1]
    r = rng.random()
    if r < 0.55: return invoice_no                  # clean
    if r < 0.75: return f"INV{digits}"              # dash dropped
    if r < 0.85: return digits.lstrip("0")          # number only
    if r < 0.92: return f"{invoice_no} PAYMENT"     # extra words
    return None                                     # customer forgot the reference


def messy_name(rng, name):
    r = rng.random()
    if r < 0.5: return name.upper()
    if r < 0.8: return name.split()[0].upper() + " ENT"
    return name.upper()[:12]


def make_payment(rng, customer, invoice, amount=None):
    delay = max(-3, round(rng.gauss(3, 5)))         # normal, mostly-on-time behaviour
    paid = min(invoice.due_date + timedelta(days=delay), AS_OF)
    ref = messy_reference(rng, invoice.invoice_no)
    return Payment(
        customer_id=customer.id,
        reference=ref,
        amount=invoice.amount if amount is None else amount,
        paid_date=paid,
        bank_narration=f"TRF FROM {messy_name(rng, customer.name)} / {ref or 'NO REF'}",
    )


def main():
    rng = random.Random(SEED)
    with SessionLocal() as s:
        wipe(s)

        customers = build_customers(rng)
        s.add_all(customers)
        s.flush()                                   # assigns customer ids
        by_id = {c.id: c for c in customers}

        invoices = build_invoices(rng, customers)
        s.add_all(invoices)
        s.flush()                                   # assigns invoice ids

        # Decide who is leaky BEFORE creating payments. Slices of one shuffled
        # list cannot overlap, so no invoice gets two leak types.
        pool = invoices[:]
        rng.shuffle(pool)
        unpaid = pool[:N_UNPAID]
        partial = pool[N_UNPAID:N_UNPAID + N_PARTIAL]
        duplicate = pool[N_UNPAID + N_PARTIAL:N_UNPAID + N_PARTIAL + N_DUPLICATE]
        unpaid_ids = {i.id for i in unpaid}
        partial_ids = {i.id for i in partial}
        duplicate_ids = {i.id for i in duplicate}

        payments, pending = [], []                  # pending = leaks waiting for payment ids

        for inv in invoices:
            cust = by_id[inv.customer_id]
            if inv.id in unpaid_ids:
                pending.append(("unpaid_invoice", inv, [], "No payment ever received"))
                continue

            if inv.id in partial_ids:
                if rng.random() < 0.5:
                    shortfall = money(inv.amount * Decimal("0.05"))   # looks like withholding tax
                else:
                    shortfall = money(rng.choice([50, 107.5, 215]))   # looks like bank charges
                pay = make_payment(rng, cust, inv, amount=inv.amount - shortfall)
                payments.append(pay)
                pending.append(("partial_payment", inv, [pay], f"Short by {shortfall}"))
                continue

            pay = make_payment(rng, cust, inv)
            payments.append(pay)

            if inv.id in duplicate_ids:
                dup = Payment(
                    customer_id=pay.customer_id,
                    reference=pay.reference,
                    amount=pay.amount,
                    paid_date=min(pay.paid_date + timedelta(days=rng.randint(2, 6)), AS_OF),
                    bank_narration=pay.bank_narration,
                )
                payments.append(dup)
                pending.append(("duplicate_payment", inv, [pay, dup], "Same payment received twice"))

        s.add_all(payments)
        s.flush()                                   # assigns payment ids

        # The bank sees every payment, including the duplicates (real money moved).
        s.add_all([
            BankTransaction(txn_date=p.paid_date, amount=p.amount,
                            direction="credit", narration=p.bank_narration)
            for p in payments
        ])

        # Write the answer key.
        s.add_all([
            PlantedLeak(
                leak_type=kind,
                record_ids={"invoice_ids": [inv.id], "payment_ids": [p.id for p in pays]},
                description=desc,
            )
            for kind, inv, pays, desc in pending
        ])
        s.commit()

        print("customers:", s.scalar(select(func.count()).select_from(Customer)))
        print("invoices: ", s.scalar(select(func.count()).select_from(Invoice)))
        print("payments: ", s.scalar(select(func.count()).select_from(Payment)))
        print("leaks:    ", dict(Counter(k for k, *_ in pending)))


if __name__ == "__main__":
    main()