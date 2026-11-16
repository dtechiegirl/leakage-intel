import random

from sqlalchemy import select

from app.db import SessionLocal
from app.models import Payment

SEED = 7
SHARE_NO_CUSTOMER = 0.15


def main():
    rng = random.Random(SEED)
    with SessionLocal() as s:
        blanked = 0
        for pay in s.scalars(select(Payment).order_by(Payment.id)):
            if rng.random() < SHARE_NO_CUSTOMER:
                pay.customer_id = None
                blanked += 1
        s.commit()
        print("payments with customer removed:", blanked)


if __name__ == "__main__":
    main()