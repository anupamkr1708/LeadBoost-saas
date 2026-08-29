"""
Atomic daily lead-quota reservation.

Production-robustness hardening, Phase A5 (atomic daily quota).

CONFIRMED GAP: `SubscriptionService.get_organization_usage()` /
`_get_daily_usage()` computes remaining quota with a plain `COUNT(*)`
over `Lead` rows. Two concurrent requests for the same organization can
both read the same "remaining = N" snapshot before either commits its
new lead(s), so both proceed -- exactly the race described in the spec:

    remaining quota = 10
    Request A reads 10   Request B reads 10
    Both accept 10        -> organization ends up over its daily limit.

This module does NOT change `get_organization_usage()` /
`_get_daily_usage()` at all -- those remain the unchanged, accurate,
COUNT(*)-based source of truth for *display* (the "remaining leads
today" figure shown to the user, and every other existing caller of
`SubscriptionService`). This module adds a separate, additive
*enforcement* gate that lead-creation call sites call immediately
before actually creating a lead, closing the race without touching
billing/plan logic at all.

How it's atomic: `reserve_daily_lead_quota` issues a single
    UPDATE daily_lead_quota_usage
    SET leads_reserved = leads_reserved + :n
    WHERE organization_id = :org AND usage_date = :today
      AND leads_reserved + :n <= :max_per_day
and checks `rowcount`. A single UPDATE's WHERE-evaluation-and-SET is
applied as one atomic unit by the database engine itself -- this is
true identically on SQLite and PostgreSQL, so it needs no
`SELECT ... FOR UPDATE` (whose semantics differ across those two
dialects) and no new infrastructure. Two concurrent reservations for
the same organization are simply serialized by the database on the same
row; a reservation that would push the org over its limit gets
`rowcount == 0` and is rejected, whichever request happens to run second.

Known, intentional trade-off (documented rather than hidden): this
reservation commits immediately, separately from the `create_lead()`
call that follows it (every existing write helper in
core/infrastructure/database/crud.py already commits internally --
see create_lead/update_lead/delete_lead -- so matching that convention
here, rather than changing crud.py's transaction behavior, was judged
the smaller, safer change). In the extremely rare case where the
reservation succeeds but the subsequent `create_lead()` call itself
raises before completing, that one quota unit is not refunded for the
rest of the day. This fails in the conservative direction (an
organization could very rarely end up with one fewer lead of quota than
it's actually entitled to) rather than the unsafe one (never allows the
race this module exists to close).
"""

import os
from datetime import date
from typing import Optional

from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from core.domain.models.billing import DailyLeadQuotaUsage
from core.infrastructure.logging import get_logger

logger = get_logger("core.infrastructure.billing.quota_guard")


def reserve_daily_lead_quota(
    db: Session, organization_id: int, max_per_day: int, count: int = 1
) -> bool:
    """
    Atomically reserves `count` units of today's daily lead quota for
    `organization_id`. Returns True if the reservation succeeded (the
    caller should proceed to create `count` lead(s) now), False if doing
    so would exceed `max_per_day` (the caller must not create the
    lead(s) and should surface the existing quota-exceeded error path).

    Call this immediately before the corresponding `create_lead()`
    call(s) for the units just reserved.
    """
    if count <= 0:
        return True

    today = date.today()
    _ensure_today_row_exists(db, organization_id, today)

    result = db.execute(
        update(DailyLeadQuotaUsage)
        .where(
            DailyLeadQuotaUsage.organization_id == organization_id,
            DailyLeadQuotaUsage.usage_date == today,
            (DailyLeadQuotaUsage.leads_reserved + count) <= max_per_day,
        )
        .values(leads_reserved=DailyLeadQuotaUsage.leads_reserved + count)
    )
    db.commit()
    return result.rowcount == 1


def _ensure_today_row_exists(db: Session, organization_id: int, today: date) -> None:
    """Idempotent get-or-create for today's counter row. A race here (two
    concurrent requests both find no row and both try to insert one) is
    self-healing: the UNIQUE(organization_id, usage_date) constraint
    causes the loser to hit IntegrityError, which is simply treated as
    'someone else already created it' rather than an error."""
    existing = (
        db.query(DailyLeadQuotaUsage)
        .filter(
            DailyLeadQuotaUsage.organization_id == organization_id,
            DailyLeadQuotaUsage.usage_date == today,
        )
        .first()
    )
    if existing is not None:
        return

    db.add(
        DailyLeadQuotaUsage(organization_id=organization_id, usage_date=today, leads_reserved=0)
    )
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
