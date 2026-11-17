"""Dataset 2: the same fake company, but with much messier payments.

Run:  python -m data_gen.generate_hard
Truth file written: data_truth_hard.csv

WARNING: like generate.py, this wipes the database and refills it.
To get dataset 1 back, run data_gen.generate then data_gen.harden again.
"""
import csv
import random
from collections import Counter
from datetime import timedelta
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import func, select

from app.db import SessionLocal
from app.models import BankTransaction, Customer, Invoice, Payment, PlantedLeak
from data_gen.generate import (AS_OF, BRANCHES, INDUSTRIES, N_CUSTOMERS,
                               N_DUPLICATE, N_PARTIAL, N_UNPAID, SUFFIXES,
                               build_invoices, messy_name, money, wipe)

SEED = 99                      # different from dataset 1 (42 and 7)
SHARE_NO_CUSTOMER = 0.25       # dataset 1 was 0.15
N_COLLISIONS = 10              # pairs of invoices from different customers with the SAME amount
N_INSTALLMENT = 8              # SWITCH: invoices paid in two parts (not a leak). 0 = off.

# Only 8 surnames, so many customers look alike (e.g. "Okafor Foods" vs "Okafor Motors").
HARD_SURNAMES = ["Okafor", "Bello", "Yusuf", "Chukwu", "Eze", "Musa", "Adeyemi", "Lawal"]


def build_customers_hard(rng):
    customers, used = [], set()
    while len(customers) < N_CUSTOMERS:
        name = f"{rng.choice(HARD_SURNAMES)} {rng.choice(SUFFIXES)}"
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


def typo(rng, text):
    """30% of the time, drop or swap one letter in the middle of the text."""
    if len(text) < 6 or rng.random() > 0.30:
        return text
    i = rng.randrange(1, len(text) - 2)
    if rng.random() < 0.5:
        return text[:i] + text[i + 1:]                     # dropped letter
    return text[:i] + text[i + 1] + text[i] + text[i + 2:]  # swapped letters


def hard_reference(rng, invoice_no):
    digits = invoice_no.split("-")[1]
    r = rng.random()
    if r < 0.40: return invoice_no                           # clean
    if r < 0.55: return f"INV{digits}"                       # dash dropped
    if r < 0.62: return digits.lstrip("0")                   # number only
    if r < 0.68: return f"{invoice_no} PAYMENT"              # extra words
    if r < 0.73: return f"INV-{int(digits) + 1:05d}"         # WRONG number (typo)
    return None                                              # forgot the reference (27%)


def hard_payment(rng, customer, invoice, amount=None):
    delay = max(-3, round(rng.gauss(10, 30)))                # some pay very late
    paid = min(invoice.due_date + timedelta(days=delay), AS_OF)
    ref = hard_reference(rng, invoice.invoice_no)
    name = typo(rng, messy_name(rng, customer.name))
    return Payment(
        customer_id=customer.id,
        reference=ref,
        amount=invoice.amount if amount is None else amount,
        paid_date=paid,
        bank_narration=f"TRF FROM {name} / {ref or 'NO REF'}",
    )


def main():
    rng = random.Random(SEED)
    with SessionLocal() as s:
        wipe(s)

        customers = build_customers_hard(rng)
        s.add_all(customers)
        s.flush()
        by_id = {c.id: c for c in customers}

        invoices = build_invoices(rng, customers)

        # Collisions: different customers, identical invoice amount.
        for _ in range(N_COLLISIONS):
            a, b = rng.sample(invoices, 2)
            if a.customer_id != b.customer_id:
                b.amount = a.amount

        s.add_all(invoices)
        s.flush()

        pool = invoices[:]
        rng.shuffle(pool)
        unpaid = pool[:N_UNPAID]
        partial = pool[N_UNPAID:N_UNPAID + N_PARTIAL]
        duplicate = pool[N_UNPAID + N_PARTIAL:N_UNPAID + N_PARTIAL + N_DUPLICATE]
        start = N_UNPAID + N_PARTIAL + N_DUPLICATE
        installment = pool[start:start + N_INSTALLMENT]   # never overlaps a leak
        unpaid_ids = {i.id for i in unpaid}
        partial_ids = {i.id for i in partial}
        duplicate_ids = {i.id for i in duplicate}

        payments, pending, truth = [], [], []

        for inv in invoices:
            cust = by_id[inv.customer_id]
            if inv.id in unpaid_ids:
                pending.append(("unpaid_invoice", inv, [], "No payment ever received"))
                continue

            if inv.id in partial_ids:
                r = rng.random()
                if r < 0.4:
                    shortfall = money(inv.amount * Decimal("0.05"))    # withholding tax
                elif r < 0.7:
                    shortfall = money(inv.amount * Decimal("0.12"))    # big short-pay
                else:
                    shortfall = money(rng.choice([50, 107.5, 215]))    # bank charges
                pay = hard_payment(rng, cust, inv, amount=inv.amount - shortfall)
                payments.append(pay)
                truth.append((pay, inv))
                pending.append(("partial_payment", inv, [pay], f"Short by {shortfall}"))
                continue

            pay = hard_payment(rng, cust, inv)
            payments.append(pay)
            truth.append((pay, inv))

            if inv.id in duplicate_ids:
                dup = Payment(
                    customer_id=pay.customer_id,
                    reference=pay.reference,
                    amount=pay.amount,
                    paid_date=min(pay.paid_date + timedelta(days=rng.randint(2, 6)), AS_OF),
                    bank_narration=pay.bank_narration,
                )
                payments.append(dup)
                truth.append((dup, inv))
                pending.append(("duplicate_payment", inv, [pay, dup], "Same payment received twice"))

        # Remove the customer from a share of payments (like harden.py did for dataset 1).
        blanked = 0
        for pay in payments:
            if rng.random() < SHARE_NO_CUSTOMER:
                pay.customer_id = None
                blanked += 1

        # TRAP: installments. Done last, with its own random stream, so turning it on
        # changes ONLY these invoices and nothing else in the dataset.
        # Each chosen invoice's normal payment is split into two smaller payments that
        # add up exactly to the invoice. This is NOT a leak, so no PlantedLeak row.
        if installment:
            rng_inst = random.Random(SEED + 1)
            first_payment = {inv.id: pay for pay, inv in truth}
            for inv in installment:
                pay1 = first_payment[inv.id]
                share = Decimal(str(rng_inst.choice([0.4, 0.5, 0.6])))
                first_amount = money(inv.amount * share)
                pay2 = Payment(
                    customer_id=pay1.customer_id,
                    reference=pay1.reference,
                    amount=inv.amount - first_amount,
                    paid_date=min(pay1.paid_date + timedelta(days=rng_inst.randint(10, 30)), AS_OF),
                    bank_narration=pay1.bank_narration,
                )
                pay1.amount = first_amount
                payments.append(pay2)
                truth.append((pay2, inv))

        s.add_all(payments)
        s.flush()

        with open("data_truth_hard.csv", "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["payment_id", "invoice_id"])
            for pay, inv in truth:
                w.writerow([pay.id, inv.id])

        s.add_all([
            BankTransaction(txn_date=p.paid_date, amount=p.amount,
                            direction="credit", narration=p.bank_narration)
            for p in payments
        ])
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
        print("payments with customer removed:", blanked)
        print("invoices paid in installments:", len(installment))
        print("leaks:    ", dict(Counter(k for k, *_ in pending)))


if __name__ == "__main__":
    main()