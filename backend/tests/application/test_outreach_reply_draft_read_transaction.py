"""
C9.5 hardening (c) -- reading a reply draft holds no database transaction (and so no pooled connection) while it
waits for the Mailer.

`outreach_service.get_reply_draft` reads the action (which begins a transaction on the request's session) and then
awaits the Mailer. The browser polls that read while a draft is being generated (2 s base, 15 s max, 30 attempts), and
a slow Mailer can keep each call open for MAILING_AGENT_TIMEOUT_SECONDS (default 15 s). Left open, every poll in
flight pins one pooled connection "idle in transaction" for the whole wait. The convention in
outreach_mailer_state.get_outreach_mailer_state is: copy what is needed, `db.rollback()`, then call the Mailer.

These tests look at the session at the moment the service hands control to the REAL Mailer client (a pass-through spy
records the session state and then awaits the unchanged client over FakeMailer's transport), once through the service
function and once through the real endpoint with the request's own session. They also pin that every result, status
code and error mapping is the one it was before. Nothing is short-circuited or mocked.
"""

import asyncio

import pytest

from application.services import outreach_service
from application.services.outreach_service import OutreachError, OutreachErrorCode
from core.infrastructure import database as database_module
from core.infrastructure.mailing_agent import reply_draft_client as mc_drafts
from core.infrastructure.database import SessionLocal, get_db
import main
from tests.application.fake_mailer import fake_content_digest
from tests.application.test_outreach_dispatch_l1 import _account, _approved, _env, _lead
# fixtures + the one-tenant/one-reply helper of the sibling test module (same style, same fakes)
from tests.application.test_outreach_reply_content_approval import (  # noqa: F401  (fixtures are used by name)
    API, DRAFTS, Flow, _legacy, client, mailer,
)

SUBJECT, BODY = "Re: Hello", "Hi Jane,\n\nExactly this text.\n\nBest"


# ------------------------------------------------------------------ observing the session during the Mailer call


def _pool_checked_out():
    """Connections currently checked out of the engine's pool, or None when the pool type cannot say."""
    pool = getattr(getattr(database_module, "engine", None), "pool", None)
    fn = getattr(pool, "checkedout", None)
    return fn() if callable(fn) else None


class Observed:
    """The request session's state at each moment the service handed control to the Mailer client."""

    def __init__(self):
        self.at_call = []          # (session_in_transaction, pool_checked_out)


def _observe(monkeypatch, session_holder):
    """A pass-through spy around the real client function: it records the session state, then awaits the REAL
    client (over FakeMailer's transport). Nothing is faked or short-circuited; it also sees calls that the
    FakeMailer fails (its own `after_call` hook does not run for injected faults)."""
    obs = Observed()
    real = obs.real = mc_drafts.fetch_reply_draft

    async def spy(*args, **kwargs):
        s = session_holder[-1]
        obs.at_call.append((s.in_transaction(), _pool_checked_out()))
        return await real(*args, **kwargs)

    monkeypatch.setattr(mc_drafts, "fetch_reply_draft", spy)
    return obs


def read_via_service(f, mailer, monkeypatch):
    """Call the service function with a session of our own. Returns (outcome, observation, in_tx_after, mailer_gets)."""
    db = SessionLocal()
    obs = _observe(monkeypatch, [db])
    gets = len(mailer.calls_to("GET", DRAFTS))
    try:
        try:
            outcome = asyncio.run(outreach_service.get_reply_draft(db, organization_id=f.org, action_id=f.aid))
        except OutreachError as exc:
            outcome = exc
        return outcome, obs, db.in_transaction(), len(mailer.calls_to("GET", DRAFTS)) - gets
    finally:
        monkeypatch.setattr(mc_drafts, "fetch_reply_draft", obs.real)
        db.close()


def read_via_endpoint(f, mailer, client, monkeypatch):
    """GET the real endpoint, with the request's own session captured so the spy can inspect it."""
    sessions = []

    def _get_db():
        s = SessionLocal()
        sessions.append(s)
        try:
            yield s
        finally:
            s.close()

    main.app.dependency_overrides[get_db] = _get_db
    obs = _observe(monkeypatch, sessions)
    gets = len(mailer.calls_to("GET", DRAFTS))
    try:
        r = client.get(f"{API}/{f.aid}/reply-draft", headers=f.h)
    finally:
        monkeypatch.setattr(mc_drafts, "fetch_reply_draft", obs.real)
        main.app.dependency_overrides.pop(get_db, None)
    return r, obs, len(mailer.calls_to("GET", DRAFTS)) - gets


# ------------------------------------------------------------------ the four Mailer-facing situations

def _generating(f, mailer):
    assert f.start().status_code == 200
    return {"view": dict(state="generating", subject=None, body=None, content_sha256=None),
            "http": (200, {"state": "generating", "subject": None, "body": None, "content_sha256": None})}


def _held(f, mailer):
    assert f.start().status_code == 200
    digest = f.generate(subject=SUBJECT, body=BODY)
    assert digest == fake_content_digest(SUBJECT, BODY)
    full = dict(state="awaiting_approval", subject=SUBJECT, body=BODY, content_sha256=digest)
    return {"view": full, "http": (200, full)}


def _released(f, mailer):
    assert f.start().status_code == 200
    digest = f.generate(subject=SUBJECT, body=BODY)
    assert f.approve(digest).status_code == 200
    assert f.dispatch().json()["state"] == "submitted"
    assert f.rec()["state"] == "queued"
    full = dict(state="queued", content_sha256=digest)       # released: the Mailer now reports it as queued
    return {"view": full, "http": (200, full)}


def _unavailable(f, mailer):
    f.to_draft()
    mailer.fail_next(503)
    return {"error": OutreachErrorCode.REPLY_DRAFT_UNAVAILABLE, "http": (502, "reply_draft_unavailable")}


def _no_draft_at_mailer(f, mailer):
    f.to_draft()
    del mailer.dispatches[(f.tenant, f.key)]                 # the Mailer genuinely has nothing under this key
    return {"error": OutreachErrorCode.REPLY_DRAFT_NOT_READY, "http": (409, "reply_draft_not_ready")}


SCENARIOS = [
    pytest.param(_generating, id="generating"),
    pytest.param(_held, id="held-awaiting-approval"),
    pytest.param(_released, id="released"),
    pytest.param(_unavailable, id="mailer-unavailable"),
    pytest.param(_no_draft_at_mailer, id="no-draft-at-the-mailer"),
]


def _check_view(outcome, expected):
    assert not isinstance(outcome, OutreachError), outcome
    for k, v in expected["view"].items():
        assert getattr(outcome, k) == v, (k, getattr(outcome, k), v)


# ====================================================== 1. the transaction is closed during the Mailer call


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_service_holds_no_transaction_during_the_mailer_call_and_returns_what_it_always_did(
    client, mailer, monkeypatch, scenario
):
    f = Flow(client, mailer, monkeypatch, "tx1")
    expected = scenario(f, mailer)
    outcome, obs, in_tx_after, gets = read_via_service(f, mailer, monkeypatch)

    # the Mailer was asked exactly once (one real GET for this draft) ...
    assert gets == 1 and len(obs.at_call) == 1, "the read makes exactly one Mailer call"
    # ... and at that moment the session was NOT inside a transaction and held no pooled connection
    in_tx, checked_out = obs.at_call[0]
    assert in_tx is False, "get_reply_draft kept its read transaction open across the Mailer call"
    if checked_out is not None:
        assert checked_out == 0, "a pooled connection was still checked out during the Mailer call"
    # nothing reopened a transaction afterwards either (the expired action is never touched again)
    assert in_tx_after is False

    if "error" in expected:
        assert isinstance(outcome, OutreachError) and outcome.error_code == expected["error"]
    else:
        _check_view(outcome, expected)


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_endpoint_holds_no_transaction_during_the_mailer_call_and_answers_exactly_as_before(
    client, mailer, monkeypatch, scenario
):
    f = Flow(client, mailer, monkeypatch, "tx2")
    expected = scenario(f, mailer)
    r, obs, gets = read_via_endpoint(f, mailer, client, monkeypatch)

    assert gets == 1 and len(obs.at_call) == 1
    in_tx, checked_out = obs.at_call[0]
    assert in_tx is False, "the request's session was still in a transaction during the Mailer call"
    if checked_out is not None:
        assert checked_out == 0, "the request still held a pooled connection during the Mailer call"

    status, want = expected["http"]
    assert r.status_code == status, r.text
    if status == 200:
        j = r.json()
        assert set(j) == {"state", "subject", "body", "content_sha256", "draft_version", "updated_at"}
        for k, v in want.items():
            assert j[k] == v, (k, j[k], v)
    else:
        assert r.json()["detail"]["error_code"] == want
        assert "injected" not in r.text and "503" not in r.text


def test_repeated_polls_keep_the_pool_empty_during_every_call_and_change_nothing(client, mailer, monkeypatch):
    """The browser polls while a draft is generating. Each poll must leave the pool as it found it."""
    f = Flow(client, mailer, monkeypatch, "tx3")
    f.to_draft()
    before_row, calls = f.row(), len(mailer.calls)
    seen = []
    for _ in range(6):
        r, obs, gets = read_via_endpoint(f, mailer, client, monkeypatch)
        assert r.status_code == 200 and r.json()["state"] == "awaiting_approval" and gets == 1
        seen += [c[0] for c in obs.at_call]
    assert seen == [False] * 6
    assert f.row() == before_row, "a read mutates nothing"
    assert len(mailer.calls) - calls == 6 and all(c.method == "GET" for c in mailer.calls[calls:])


# ====================================================== 2. paths that never reach the Mailer are unchanged


def test_not_started_cancelled_and_legacy_make_no_mailer_call_and_answer_as_before(client, mailer, monkeypatch):
    f = Flow(client, mailer, monkeypatch, "tx4")
    calls = len(mailer.calls)
    outcome, obs, _, gets = read_via_service(f, mailer, monkeypatch)
    assert outcome.state == "not_started" and obs.at_call == [] and gets == 0

    assert f.cancel().status_code == 200                          # PENDING_REVIEW -> CANCELLED, nothing at the Mailer
    calls = len(mailer.calls)
    outcome, obs, _, gets = read_via_service(f, mailer, monkeypatch)
    assert outcome.state == "cancelled" and obs.at_call == [] and gets == 0 and len(mailer.calls) == calls

    g = Flow(client, mailer, monkeypatch, "tx4l")
    _legacy(g, "approved")
    calls = len(mailer.calls)
    outcome, obs, _, gets = read_via_service(g, mailer, monkeypatch)
    assert outcome.state == "legacy" and obs.at_call == [] and gets == 0 and len(mailer.calls) == calls


def test_a_foreign_tenant_still_gets_a_404_and_causes_no_mailer_traffic(client, mailer, monkeypatch):
    f = Flow(client, mailer, monkeypatch, "tx5")
    f.to_draft()
    h2, org2, uid2, tenant2 = _env(client, mailer, monkeypatch, "tx5b")
    calls = len(mailer.calls)
    r = client.get(f"{API}/{f.aid}/reply-draft", headers=h2)
    assert r.status_code == 404 and len(mailer.calls) == calls

    db = SessionLocal()
    try:
        with pytest.raises(OutreachError) as ei:
            asyncio.run(outreach_service.get_reply_draft(db, organization_id=org2, action_id=f.aid))
        assert ei.value.error_code == OutreachErrorCode.ACTION_NOT_FOUND
    finally:
        db.close()
    assert len(mailer.calls) == calls


def test_an_ordinary_non_reply_action_is_still_refused_without_a_mailer_call(client, mailer, monkeypatch):
    h, org, uid, tenant = _env(client, mailer, monkeypatch, "tx6")
    acc = _account(client, h, org, mailer)
    aid = _approved(client, h, _lead(org, uid), acc["id"])
    calls = len(mailer.calls)
    r = client.get(f"{API}/{aid}/reply-draft", headers=h)
    assert r.status_code == 409 and r.json()["detail"]["error_code"] == "not_a_reply"
    db = SessionLocal()
    try:
        with pytest.raises(OutreachError) as ei:
            asyncio.run(outreach_service.get_reply_draft(db, organization_id=org, action_id=aid))
        assert ei.value.error_code == OutreachErrorCode.NOT_A_REPLY
    finally:
        db.close()
    assert len(mailer.calls) == calls
