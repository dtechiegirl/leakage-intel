from typing import Optional
from datetime import date
from decimal import Decimal
from sqlalchemy import ForeignKey, Numeric, String, Date, Index, CheckConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.db import Base


class Customer(Base):
    __tablename__ = "customers"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    branch: Mapped[str] = mapped_column(String(100), index=True)
    industry: Mapped[str] = mapped_column(String(100))
    payment_terms_days: Mapped[int] = mapped_column(default=30)

    invoices: Mapped[list["Invoice"]] = relationship(back_populates="customer")


class Invoice(Base):
    __tablename__ = "invoices"
    __table_args__ = (
        CheckConstraint("amount > 0", name="invoice_amount_positive"),
        Index("ix_invoices_customer_due", "customer_id", "due_date"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    customer_id: Mapped[Optional[int]] = mapped_column(ForeignKey("customers.id"), index=True)
    invoice_no: Mapped[str] = mapped_column(String(50), unique=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    currency: Mapped[str] = mapped_column(String(3), default="NGN")
    issue_date: Mapped[date] = mapped_column(Date)
    due_date: Mapped[date] = mapped_column(Date)

    customer: Mapped["Customer"] = relationship(back_populates="invoices")



class Payment(Base):
    __tablename__ = "payments"

    id: Mapped[int] = mapped_column(primary_key=True)
    customer_id: Mapped[Optional[int]] = mapped_column(ForeignKey("customers.id"), index=True)
    reference: Mapped[Optional[str]] = mapped_column(String(100))
    bank_narration: Mapped[Optional[str]] = mapped_column(String(300))
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    currency: Mapped[str] = mapped_column(String(3), default="NGN")
    paid_date: Mapped[date] = mapped_column(Date, index=True)
    # bank_narration: Mapped[Optional[str]] = mapped_column(String(300))


class BankTransaction(Base):
    __tablename__ = "bank_transactions"

    id: Mapped[int] = mapped_column(primary_key=True)
    txn_date: Mapped[date] = mapped_column(Date, index=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    direction: Mapped[str] = mapped_column(String(6))  # "credit" or "debit"
    narration: Mapped[str] = mapped_column(String(300))


class Expense(Base):
    __tablename__ = "expenses"

    id: Mapped[int] = mapped_column(primary_key=True)
    vendor: Mapped[str] = mapped_column(String(200), index=True)
    category: Mapped[str] = mapped_column(String(100))
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    expense_date: Mapped[date] = mapped_column(Date)
    branch: Mapped[str] = mapped_column(String(100), index=True)

class PaymentAllocation(Base):
    """The matching engine's guesses: which payment settles which invoice.
    Kept separate from the raw data so the engine never rewrites its inputs."""
    __tablename__ = "payment_allocations"

    id: Mapped[int] = mapped_column(primary_key=True)
    payment_id: Mapped[int] = mapped_column(ForeignKey("payments.id"), unique=True)
    invoice_id: Mapped[int] = mapped_column(ForeignKey("invoices.id"), index=True)
    method: Mapped[str] = mapped_column(String(30))
    confidence: Mapped[Decimal] = mapped_column(Numeric(3, 2))

class PlantedLeak(Base):
    """GROUND TRUTH. Only the eval may read this table. Detection code must never touch it."""
    __tablename__ = "planted_leaks"

    id: Mapped[int] = mapped_column(primary_key=True)
    leak_type: Mapped[str] = mapped_column(String(50), index=True)
    record_ids: Mapped[dict] = mapped_column(JSONB)
    description: Mapped[str] = mapped_column(String(500))

class Alert(Base):
    """What the detectors found. One row per problem."""
    __tablename__ = "alerts"

    id: Mapped[int] = mapped_column(primary_key=True)
    alert_type: Mapped[str] = mapped_column(String(50), index=True)
    invoice_id: Mapped[int] = mapped_column(ForeignKey("invoices.id"), index=True)
    payment_ids: Mapped[list] = mapped_column(JSONB)
    amount_at_risk: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    evidence: Mapped[dict] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(20), default="open")