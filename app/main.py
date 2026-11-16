from typing import Optional

from fastapi import FastAPI, HTTPException
from sqlalchemy import select

from app.db import SessionLocal
from app.models import Alert
from app.overview import build_facts

app = FastAPI(title="Leakage Intel")


def alert_to_dict(a):
    return {
        "id": a.id,
        "alert_type": a.alert_type,
        "invoice_id": a.invoice_id,
        "amount_at_risk": str(a.amount_at_risk),
        "status": a.status,
    }


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/alerts")
def list_alerts(alert_type: Optional[str] = None, limit: int = 50):
    query = select(Alert)
    if alert_type:
        query = query.where(Alert.alert_type == alert_type)
    query = query.order_by(Alert.amount_at_risk.desc()).limit(limit)
    with SessionLocal() as s:
        return [alert_to_dict(a) for a in s.scalars(query)]


@app.get("/alerts/{alert_id}")
def get_alert(alert_id: int):
    with SessionLocal() as s:
        a = s.get(Alert, alert_id)
        if a is None:
            raise HTTPException(status_code=404, detail="Alert not found")
        data = alert_to_dict(a)
        data["payment_ids"] = a.payment_ids
        data["evidence"] = a.evidence
        return data


@app.get("/summary")
def summary():
    return build_facts()