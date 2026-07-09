"""Refund bookkeeping.

When a booking is cancelled a refund is calculated from its price and the
applicable notice tier, then written to the refund ledger with a processed
status. Amounts are stored in whole cents.
"""
from datetime import datetime

from sqlalchemy.orm import Session

from ..models import Booking, RefundLog


def log_refund(db: Session, booking: Booking, percent: int) -> RefundLog:
    # BUGFIX (rule 6): round half-cents UP with integer math. The previous
    # float truncation (int(refund_dollars * 100)) rounded down and could also
    # disagree with the amount returned to the caller. This formula is identical
    # to the one used in the cancel response, so the two amounts always match.
    amount_cents = (booking.price_cents * percent + 50) // 100
    entry = RefundLog(
        booking_id=booking.id,
        amount_cents=amount_cents,
        status="processed",
        processed_at=datetime.utcnow(),
    )
    db.add(entry)
    db.commit()
    db.refresh(entry)
    return entry
