from collections import defaultdict
from datetime import date
from decimal import Decimal

from sqlalchemy import delete, select

from app.db import SessionLocal
from app.models import Alert, Invoice, Payment, PaymentAllocation

AS_OF = date(2026, 10, 31)       # must match the generator's AS_OF
TOLERANCE = Decimal("0.01")      # ignore rounding differences under 1 kobo


def evidence_for(inv, pays):
    return {
        "invoice_no": inv.invoice_no,
        "invoice_amount": str(inv.amount),
        "due_date": inv.due_date.isoformat(),
        "payments": [
            {"id": p.id, "amount": str(p.amount),
             "date": p.paid_date.isoformat(), "reference": p.reference}
            for p in pays
        ],
    }


def detect():
    with SessionLocal() as s:
        s.execute(delete(Alert))   # safe to re-run

        invoices = s.scalars(select(Invoice)).all()
        rows = s.execute(
            select(PaymentAllocation.invoice_id, Payment)
            .join(Payment, Payment.id == PaymentAllocation.payment_id)
        ).all()

        paid = defaultdict(list)
        for invoice_id, p in rows:
            paid[invoice_id].append(p)

        alerts = []
        for inv in invoices:
            pays = sorted(paid.get(inv.id, []), key=lambda p: (p.paid_date, p.id))
            total = sum((p.amount for p in pays), Decimal("0"))
            ev = evidence_for(inv, pays)

            if not pays:
                if inv.due_date < AS_OF:
                    alerts.append(Alert(
                        alert_type="unpaid_invoice", invoice_id=inv.id,
                        payment_ids=[], amount_at_risk=inv.amount, evidence=ev))
            elif len(pays) >= 2 and total > inv.amount + TOLERANCE:
                alerts.append(Alert(
                    alert_type="duplicate_payment", invoice_id=inv.id,
                    payment_ids=[p.id for p in pays],
                    amount_at_risk=total - inv.amount, evidence=ev))
            elif total < inv.amount - TOLERANCE:
                alerts.append(Alert(
                    alert_type="partial_payment", invoice_id=inv.id,
                    payment_ids=[p.id for p in pays],
                    amount_at_risk=inv.amount - total, evidence=ev))

        s.add_all(alerts)
        s.commit()

        counts = defaultdict(int)
        for a in alerts:
            counts[a.alert_type] += 1
        print("alerts:", dict(counts))


if __name__ == "__main__":
    detect()
