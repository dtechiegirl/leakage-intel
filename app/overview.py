import json
from decimal import Decimal

from sqlalchemy import func, select

from app.db import SessionLocal
from app.models import Alert, Customer, Invoice


def naira(x):
    return f"₦{x:,.2f}"


def build_facts():
    with SessionLocal() as s:
        # One row per alert type: how many alerts, and how much money is involved.
        rows = s.execute(
            select(Alert.alert_type, func.count(), func.sum(Alert.amount_at_risk))
            .group_by(Alert.alert_type)
        ).all()
        by_type = {t: (n, total) for t, n, total in rows}

        # The five customers with the most money still to collect.
        top = s.execute(
            select(Customer.name, Customer.branch, func.count(Alert.id),
                   func.sum(Alert.amount_at_risk))
            .join(Invoice, Invoice.customer_id == Customer.id)
            .join(Alert, Alert.invoice_id == Invoice.id)
            .where(Alert.alert_type.in_(["unpaid_invoice", "partial_payment"]))
            .group_by(Customer.id)
            .order_by(func.sum(Alert.amount_at_risk).desc())
            .limit(5)
        ).all()

    zero = Decimal("0")
    to_collect = sum((by_type.get(t, (0, zero))[1]
                      for t in ("unpaid_invoice", "partial_payment")), zero)
    to_refund = by_type.get("duplicate_payment", (0, zero))[1]

    return {
        "alerts_by_type": {t: {"count": n, "amount": naira(total)}
                           for t, (n, total) in by_type.items()},
        "total_to_collect": naira(to_collect),
        "total_to_refund": naira(to_refund),
        "top_customers_by_amount_to_collect": [
            {"customer": name, "branch": branch, "open_alerts": n, "amount": naira(total)}
            for name, branch, n, total in top
        ],
    }


if __name__ == "__main__":
    print(json.dumps(build_facts(), ensure_ascii=False, indent=2))