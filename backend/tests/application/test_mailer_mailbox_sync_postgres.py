"""
L1: PostgreSQL-only proof of the account row lock used by the Mailer reconcile.

SQLite has no row locks, so tests/application/test_mailer_mailbox_sync.py can only
show that a concurrent race still converges on ONE mailbox (via the Mailer's own
uniqueness -> 409 -> adopt). Here, on real PostgreSQL, we prove the stronger
property: `SELECT ... FOR UPDATE` serializes reconciles of one account, so the
second never even attempts a duplicate create, and a concurrent LeadBoost write
waits for the in-flight reconcile instead of racing it.

Skipped unless ALEMBIC_TEST_DATABASE_URL points at a disposable PostgreSQL
server (same gate as tests/infrastructure/test_alembic_postgres_migrations.py).
"""

import asyncio
import os
import threading
import time
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.engine import make_url

from application.services.mailer_mailbox_sync import sync_email_account
from core.domain.models.email_account import EmailAccount, MailerSyncState, VerificationStatus
from core.domain.models.organization import Organization
from core.infrastructure.database import Base
from core.infrastructure.mailing_agent import client as mc
from core.infrastructure.security.credential_crypto import encrypt_credential
from tests.application.fake_mailer import FakeMailer

_ADMIN_URL = os.environ.get("ALEMBIC_TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not _ADMIN_URL,
    reason="Set ALEMBIC_TEST_DATABASE_URL to a disposable PostgreSQL server to run the L1 row-lock tests.",
)


@pytest.fixture()
def pg():
    name = f"l1_lock_{uuid.uuid4().hex[:10]}"
    admin = create_engine(_ADMIN_URL, isolation_level="AUTOCOMMIT")
    with admin.connect() as c:
        c.execute(text(f'CREATE DATABASE "{name}"'))
    url = make_url(_ADMIN_URL).set(database=name)
    engine = create_engine(url)
    Base.metadata.create_all(bind=engine)
    try:
        yield sessionmaker(bind=engine)
    finally:
        engine.dispose()
        with admin.connect() as c:
            c.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        admin.dispose()


@pytest.fixture()
def mailer(monkeypatch):
    monkeypatch.delenv("MAILING_AGENT_ORG_API_KEYS", raising=False)
    return FakeMailer().install(monkeypatch, mc)


def _seed(Session, monkeypatch, mailer):
    s = Session()
    org = Organization(name="Lock Org")
    s.add(org); s.commit()
    tenant = mailer.map_org(monkeypatch, org.id)
    acc = EmailAccount(
        organization_id=org.id, provider="smtp", email_address="lock@example.com", smtp_host="smtp.example.com",
        smtp_port=587, security_mode="starttls", username="u", is_active=True, credential_type="smtp_password",
        encrypted_credential=encrypt_credential("pg-lock-secret"), verification_status=VerificationStatus.VERIFIED,
        verified_at=datetime.now(timezone.utc), mailer_sync_state=MailerSyncState.PENDING)
    s.add(acc); s.commit()
    ids = (org.id, acc.id, tenant)
    s.close()
    return ids


def _run_sync(Session, org_id, acc_id):
    s = Session()
    try:
        asyncio.run(sync_email_account(s, org_id, acc_id))
    finally:
        s.close()


def test_row_lock_serializes_two_reconciles_so_the_second_never_creates_a_duplicate(pg, mailer, monkeypatch):
    org_id, acc_id, tenant = _seed(pg, monkeypatch, mailer)
    mailer.latency = 0.4                      # keep the first reconcile (and its lock) in flight

    threads = [threading.Thread(target=_run_sync, args=(pg, org_id, acc_id)) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    posts = mailer.calls_to("POST", "/mailboxes")
    # SQLite shows [201, 409]; with the row lock the loser waits, sees the saved ref and only activates.
    assert [c.status for c in posts] == [201]
    assert len(mailer.tenant_boxes(tenant)) == 1
    assert len(mailer.calls_to("PATCH", "/mailboxes/")) == 1
    s = pg()
    acc = s.get(EmailAccount, acc_id)
    assert (acc.mailer_sync_state, acc.mailer_sync_error_code) == (MailerSyncState.SYNCED, None)
    assert acc.mailer_mailbox_ref == mailer.tenant_boxes(tenant)[0]["public_reference"]
    s.close()


def test_a_concurrent_leadboost_write_waits_for_the_inflight_reconcile(pg, mailer, monkeypatch):
    """A disable that arrives mid-reconcile must not interleave with it: it blocks on the row
    lock, lands after the reconcile commits, and (being a normal LeadBoost change) leaves the
    row 'pending' for the next reconcile -- there is no window where a stale ACTIVE call
    can overwrite a newer disable."""
    org_id, acc_id, tenant = _seed(pg, monkeypatch, mailer)
    mailer.latency = 0.6
    t = threading.Thread(target=_run_sync, args=(pg, org_id, acc_id))
    t.start()
    time.sleep(0.25)                                       # the reconcile holds the lock now

    s = pg()
    started = time.monotonic()
    s.execute(text("UPDATE email_accounts SET is_active = false, mailer_sync_state = 'pending' WHERE id = :i"), {"i": acc_id})
    s.commit()
    waited = time.monotonic() - started
    s.close()
    t.join(timeout=30)

    assert waited > 0.2, f"the write did not wait for the lock (waited {waited:.3f}s)"

    mailer.latency = 0
    _run_sync(pg, org_id, acc_id)                          # the follow-up reconcile applies the newer state
    assert mailer.active_boxes(tenant) == []
    s = pg()
    acc = s.get(EmailAccount, acc_id)
    assert acc.is_active is False and acc.mailer_sync_state == MailerSyncState.SYNCED
    s.close()
