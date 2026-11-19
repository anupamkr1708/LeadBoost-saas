"""
C9.5 -- exact-content approval of a managed reply (LeadBoost side).

Everything runs through the REAL HTTP API and the REAL Mailer client; only the Mailer's far end is the in-memory
FakeMailer (an httpx.MockTransport that follows the real Mailer's draft contract). So request construction, the
per-organization key, strict response parsing and error mapping are all exercised, and nothing is mocked at the
function level. The real Mailer is exercised by the cross-service E2E.

What is proven here, in plain terms: nothing is released before the user approved the exact draft they saw; a stale
or changed draft is refused; approval and its evidence are one atomic write; dispatch releases the SAME draft (no
second generation, no second dispatch); an ambiguous release is reconciled by repeating it; cancellation can never
leave a draft that will still be sent; ordinary outreach is untouched; and the browser never sees Mailer internals.
"""

import json
from datetime import datetime, timezone

import httpx
import pytest
from fastapi.testclient import TestClient

import main
from application.services import outreach_service
from core.domain.models.lead import Lead
from core.domain.models.outreach_action import OutreachAction
from core.infrastructure.database import SessionLocal, crud
from core.infrastructure.mailing_agent import client as mc
from core.infrastructure.mailing_agent import reply_draft_client as mc_drafts
from tests.application.fake_mailer import FakeMailer, fake_content_digest
from tests.application.test_outreach_dispatch_l1 import DISPATCH_PATH, _account, _approved, _dispatch, _env, _lead
from tests.application.test_outreach_reply_action import REF, REF_OTHER, _owner, _reply, _rows, _setup

API = "/api/v2/outreach-actions"
DRAFTS = "/integrations/leadboost/reply-drafts/"


@pytest.fixture()
def client():
    with TestClient(main.app) as c:
        yield c


@pytest.fixture()
def mailer(monkeypatch):
    monkeypatch.delenv("MAILING_AGENT_ORG_API_KEYS", raising=False)
    return FakeMailer().install(monkeypatch, mc)


class Flow:
    """One tenant, one lead, one sender, one reply action -- with small verbs for each step."""

    def __init__(self, client, mailer, monkeypatch, tag, ref=REF):
        self.client, self.mailer = client, mailer
        self.h, self.org, self.tenant, self.lead, self.acc = _setup(client, mailer, monkeypatch, tag)
        self.aid = _reply(client, self.h, self.lead, self.acc, ref).json()["id"]
        s = SessionLocal()
        try:
            self.key = s.get(OutreachAction, self.aid).idempotency_key
        finally:
            s.close()

    def row(self):
        s = SessionLocal()
        try:
            a = s.get(OutreachAction, self.aid)
            return {c.name: getattr(a, c.name) for c in OutreachAction.__table__.columns}
        finally:
            s.close()

    def start(self):
        return self.client.post(f"{API}/{self.aid}/reply-draft", headers=self.h)

    def draft(self):
        return self.client.get(f"{API}/{self.aid}/reply-draft", headers=self.h)

    def approve(self, digest="__none__"):
        kw = {} if digest == "__none__" else {"json": {"content_sha256": digest}}
        return self.client.post(f"{API}/{self.aid}/approve", headers=self.h, **kw)

    def dispatch(self):
        return _dispatch(self.client, self.h, self.aid)

    def cancel(self):
        return self.client.post(f"{API}/{self.aid}/cancel", headers=self.h)

    def generate(self, **kw):
        return self.mailer.generate_draft(self.tenant, self.key, **kw)

    def rec(self):
        return self.mailer.dispatch_record(self.tenant, self.key)

    def to_draft(self):
        assert self.start().status_code == 200
        return self.generate()

    def to_approved(self):
        digest = self.to_draft()
        assert self.approve(digest).status_code == 200
        return digest

    def draft_calls(self, suffix=None):
        cs = self.mailer.calls_to("POST", DRAFTS)
        return [c for c in cs if suffix is None or c.path.endswith(suffix)]


# =============================================================== creation: nothing is sent or generated


def test_reply_creation_contacts_the_mailer_not_at_all(client, mailer, monkeypatch):
    f = Flow(client, mailer, monkeypatch, "c1")
    assert mailer.calls_to("POST", DISPATCH_PATH) == [] and mailer.calls_to("GET", DRAFTS) == []
    assert f.row()["state"] == "pending_review" and f.row()["reply_draft_requested_at"] is None


def test_reply_can_be_created_for_a_lead_with_no_generated_outreach_text(client, mailer, monkeypatch):
    """build_message_snapshot used to make this impossible. The fallback stores NO invented email text."""
    h, org, uid, tenant = _env(client, mailer, monkeypatch, "c2")
    acc = _account(client, h, org, mailer)
    lead = _lead(org, uid)
    s = SessionLocal()
    try:
        s.get(Lead, lead).outreach_message = None
        s.commit()
    finally:
        s.close()
    r = _reply(client, h, lead, acc["id"], REF)
    assert r.status_code == 201, r.text
    a = r.json()
    assert a["state"] == "pending_review" and a["body"] == "" and a["subject"] is None
    assert a["reply_to_message_reference"] == REF


def test_ordinary_outreach_still_requires_its_message(client, mailer, monkeypatch):
    h, org, uid, tenant = _env(client, mailer, monkeypatch, "c3")
    acc = _account(client, h, org, mailer)
    lead = _lead(org, uid)
    s = SessionLocal()
    try:
        s.get(Lead, lead).outreach_message = None
        s.commit()
    finally:
        s.close()
    r = client.post(API, headers=h, json={"lead_id": lead, "email_account_id": acc["id"], "mode": "manual"})
    assert r.status_code == 422 and r.json()["detail"]["error_code"] == "message_not_ready"


def test_one_action_per_inbound_message_and_a_different_message_is_a_different_action(client, mailer, monkeypatch):
    f = Flow(client, mailer, monkeypatch, "c4")
    again = _reply(client, f.h, f.lead, f.acc, REF)
    assert again.status_code == 201 and again.json()["id"] == f.aid and _rows(f.org) == 1
    other = _reply(client, f.h, f.lead, f.acc, REF_OTHER)
    assert other.json()["id"] != f.aid and _rows(f.org) == 2


# =============================================================== draft generation: allowed pre-approval, idempotent


def test_start_generation_submits_the_reply_once_and_sends_nothing(client, mailer, monkeypatch):
    f = Flow(client, mailer, monkeypatch, "g1")
    r = f.start()
    assert r.status_code == 200 and r.json()["state"] == "generating"
    (call,) = mailer.calls_to("POST", DISPATCH_PATH)
    assert call.body["in_reply_to"] == {"message_reference": REF} and call.body["idempotency_key"] == f.key
    assert f.draft_calls() == [], "starting generation must not release anything"
    row = f.row()
    assert row["state"] == "pending_review", "LeadBoost's own state machine is unchanged by generation"
    assert row["reply_draft_requested_at"] is not None and row["approved_content_sha256"] is None


def test_start_generation_is_idempotent_and_never_creates_a_second_dispatch(client, mailer, monkeypatch):
    f = Flow(client, mailer, monkeypatch, "g2")
    f.start()
    first_at = f.row()["reply_draft_requested_at"]
    for _ in range(3):
        assert f.start().status_code == 200
    assert len(mailer.dispatches) == 1, "the Mailer replays the same operation; there is exactly one"
    assert f.row()["reply_draft_requested_at"] == first_at, "the first acceptance time is never rewritten"


def test_timeout_after_the_mailer_accepted_is_recovered_with_the_same_identity(client, mailer, monkeypatch):
    """The Mailer applied the request but the response never arrived. Repeating the call must reuse the operation."""
    f = Flow(client, mailer, monkeypatch, "g3")
    lost = {"n": 0}

    def drop_first_response(call):
        if call.path == DISPATCH_PATH and lost["n"] == 0:
            lost["n"] += 1
            raise httpx.ReadTimeout("response lost after the Mailer accepted")

    mailer.after_call = drop_first_response
    r = f.start()
    assert r.status_code == 502 and r.json()["detail"]["error_code"] == "reply_draft_unavailable"
    assert f.row()["reply_draft_requested_at"] is None and len(mailer.dispatches) == 1

    assert f.start().status_code == 200
    assert len(mailer.dispatches) == 1, "recovery must not create another dispatch"
    assert f.row()["reply_draft_requested_at"] is not None


def test_cannot_start_generation_once_the_action_is_no_longer_pending_review(client, mailer, monkeypatch):
    f = Flow(client, mailer, monkeypatch, "g4")
    f.cancel()
    r = f.start()
    assert r.status_code == 409 and r.json()["detail"]["error_code"] == "invalid_state_transition"
    assert mailer.calls_to("POST", DISPATCH_PATH) == []


def test_lost_race_with_cancel_during_generation_revokes_the_mailers_draft(client, mailer, monkeypatch):
    """Cancelled while the Mailer call was in flight: the Mailer may now hold a draft; it must be revoked."""
    f = Flow(client, mailer, monkeypatch, "g5")

    def cancel_in_flight(call):
        if call.path == DISPATCH_PATH:
            mailer.after_call = None
            # A concurrent request's atomic cancel claim (the sync TestClient cannot be called from inside the
            # running event loop, so the claim is performed directly -- it is the same single UPDATE).
            s = SessionLocal()
            try:
                assert crud.claim_outreach_action_for_cancellation(
                    s, organization_id=f.org, action_id=f.aid, cancelled_at=datetime.now(timezone.utc), reason="race"
                ) == 1
                s.commit()
            finally:
                s.close()

    mailer.after_call = cancel_in_flight
    r = f.start()
    assert r.status_code == 409
    assert f.row()["state"] == "cancelled"
    assert f.rec()["state"] == "cancelled", "the orphaned Mailer draft must be revoked, not left holdable"


# =============================================================== reading the draft


def test_draft_reads_through_the_states(client, mailer, monkeypatch):
    f = Flow(client, mailer, monkeypatch, "d1")
    assert f.draft().json()["state"] == "not_started"
    f.start()
    d = f.draft().json()
    assert d["state"] == "generating" and d["body"] is None and d["content_sha256"] is None
    digest = f.generate(subject="Re: Hello", body="Hi Jane,\n\nExactly this text.\n\nBest")
    d = f.draft().json()
    assert d == {"state": "awaiting_approval", "subject": "Re: Hello", "body": "Hi Jane,\n\nExactly this text.\n\nBest",
                 "content_sha256": digest, "draft_version": 1, "updated_at": d["updated_at"]}


def test_draft_read_is_the_mailers_text_never_the_leads_outreach_message(client, mailer, monkeypatch):
    f = Flow(client, mailer, monkeypatch, "d2")
    f.start()
    f.generate(body="A reply written for the inbound message.")
    s = SessionLocal()
    try:
        original = s.get(Lead, f.lead).outreach_message
    finally:
        s.close()
    body = f.draft().json()["body"]
    assert body == "A reply written for the inbound message." and original and original not in body


def test_repeated_draft_reads_mutate_nothing_and_make_no_writes_to_the_mailer(client, mailer, monkeypatch):
    f = Flow(client, mailer, monkeypatch, "d3")
    f.to_draft()
    before_row, before_rec = f.row(), json.dumps(f.rec(), sort_keys=True, default=str)
    posts = len(mailer.calls_to("POST", "/"))
    for _ in range(4):
        assert f.draft().status_code == 200
    assert f.row() == before_row and json.dumps(f.rec(), sort_keys=True, default=str) == before_rec
    assert len(mailer.calls_to("POST", "/")) == posts, "a read is a GET: it never POSTs"
    assert all(c.method == "GET" for c in mailer.calls_to("GET", DRAFTS))


def test_draft_response_is_no_store_and_leaks_no_mailer_internals(client, mailer, monkeypatch):
    f = Flow(client, mailer, monkeypatch, "d4")
    f.to_draft()
    r = f.draft()
    assert r.headers["cache-control"] == "no-store"
    action = client.get(f"{API}/{f.aid}", headers=f.h).text
    # The NEW draft endpoint carries no Mailer reference of any kind, only product data.
    for forbidden in ("mailer.test", "key-org", f.tenant, "X-API-Key", "dsp_", "in_reply_to", "grounding", "mailbox"):
        assert forbidden not in r.text, f"{forbidden!r} leaked through the draft endpoint"
    assert set(r.json()) == {"state", "subject", "body", "content_sha256", "draft_version", "updated_at"}
    # The pre-existing action response may show the Mailer's opaque PUBLIC reference (it always could, once
    # submitted); it must still never carry the key, the URL or the tenant.
    for forbidden in ("mailer.test", "key-org", f.tenant, "X-API-Key"):
        assert forbidden not in action, f"{forbidden!r} leaked through the action response"


def test_mailer_unavailable_is_a_safe_502_with_no_raw_text(client, mailer, monkeypatch):
    f = Flow(client, mailer, monkeypatch, "d5")
    f.to_draft()
    mailer.fail_next(503)
    r = f.draft()
    assert r.status_code == 502 and r.json()["detail"]["error_code"] == "reply_draft_unavailable"
    assert "injected" not in r.text and "503" not in r.text


@pytest.mark.parametrize("payload", [
    {"state": "awaiting_approval"},                                                             # content missing
    {"state": "generating", "subject": "s", "body": "b", "content_sha256": "a" * 64, "draft_version": 1},
    {"state": "awaiting_approval", "subject": "s", "body": "b", "content_sha256": "NOTHEX", "draft_version": 1},
    {"state": "made_up", "subject": None, "body": None, "content_sha256": None, "draft_version": None},
    {"state": "awaiting_approval", "subject": "s", "body": "b", "content_sha256": "a" * 64, "draft_version": 1,
     "mailbox_id": 7},                                                                         # unknown field
])
def test_schema_drift_in_the_mailer_response_is_refused_not_passed_through(client, mailer, monkeypatch, payload):
    f = Flow(client, mailer, monkeypatch, "d6")
    f.to_draft()
    original = mailer._route_draft

    def drift(call, t, b):
        return httpx.Response(200, json={**payload, "updated_at": None})

    mailer._route_draft = drift
    r = f.draft()
    mailer._route_draft = original
    assert r.status_code == 502 and r.json()["detail"]["error_code"] == "reply_draft_unavailable"


# =============================================================== approval: the exact reviewed draft


def test_cannot_approve_before_a_draft_was_requested_or_while_it_is_generating(client, mailer, monkeypatch):
    f = Flow(client, mailer, monkeypatch, "a1")
    r = f.approve("a" * 64)
    assert r.status_code == 409 and r.json()["detail"]["error_code"] == "reply_draft_not_ready"
    f.start()
    r = f.approve("a" * 64)
    assert r.status_code == 409 and r.json()["detail"]["error_code"] == "reply_draft_not_ready"
    assert f.row()["state"] == "pending_review" and f.row()["approved_content_sha256"] is None


@pytest.mark.parametrize("digest", [None, "", "abc", "A" * 64, "g" * 64, "a" * 65, 5])
def test_approval_requires_a_well_formed_digest(client, mailer, monkeypatch, digest):
    f = Flow(client, mailer, monkeypatch, "a2")
    f.to_draft()
    r = f.client.post(f"{API}/{f.aid}/approve", headers=f.h, json={"content_sha256": digest})
    assert r.status_code == 422 and f.row()["state"] == "pending_review"


def test_approval_without_a_body_is_refused_for_a_reply(client, mailer, monkeypatch):
    f = Flow(client, mailer, monkeypatch, "a3")
    f.to_draft()
    r = f.approve()
    assert r.status_code == 422 and r.json()["detail"]["error_code"] == "reply_content_hash_required"
    assert f.row()["state"] == "pending_review"


def test_the_ordinary_approval_path_can_never_approve_a_reply(client, mailer, monkeypatch):
    f = Flow(client, mailer, monkeypatch, "a4")
    f.to_draft()
    s = SessionLocal()
    try:
        with pytest.raises(outreach_service.OutreachError) as exc:
            outreach_service.approve_action(s, organization_id=f.org, action_id=f.aid, approved_by_user_id=_owner(f.org))
        assert exc.value.error_code == "reply_content_hash_required"
    finally:
        s.close()
    assert f.row()["state"] == "pending_review"


def test_stale_digest_is_refused_and_the_user_must_review_the_current_draft(client, mailer, monkeypatch):
    f = Flow(client, mailer, monkeypatch, "a5")
    seen = f.to_draft()
    f.mailer.mutate_draft(f.tenant, f.key, body="Changed after the user looked at it.")
    r = f.approve(seen)
    assert r.status_code == 409 and r.json()["detail"]["error_code"] == "reply_draft_stale"
    row = f.row()
    assert row["state"] == "pending_review" and row["approved_content_sha256"] is None and row["approved_at"] is None
    current = f.draft().json()["content_sha256"]
    assert current != seen and f.approve(current).status_code == 200


def test_a_digest_for_content_that_never_existed_is_refused(client, mailer, monkeypatch):
    f = Flow(client, mailer, monkeypatch, "a6")
    f.to_draft()
    r = f.approve(fake_content_digest("Re: Hello", "something nobody generated"))
    assert r.status_code == 409 and r.json()["detail"]["error_code"] == "reply_draft_stale"


def test_approval_stores_the_exact_digest_the_user_and_the_time_atomically(client, mailer, monkeypatch):
    f = Flow(client, mailer, monkeypatch, "a7")
    digest = f.to_draft()
    r = f.approve(digest)
    assert r.status_code == 200
    out = r.json()
    assert out["state"] == "approved" and out["approved_content_sha256"] == digest and out["approved_draft_version"] == 1
    row = f.row()
    assert row["approved_content_sha256"] == digest and row["approved_draft_version"] == 1
    assert row["approved_by_user_id"] == _owner(f.org) and row["approved_at"] is not None
    assert f.draft_calls() == [], "approval records the decision; it does not release or send anything"


def test_approval_is_not_repeatable_and_does_not_rewrite_the_evidence(client, mailer, monkeypatch):
    f = Flow(client, mailer, monkeypatch, "a8")
    digest = f.to_draft()
    f.approve(digest)
    before = f.row()
    again = f.approve(digest)
    assert again.status_code == 409 and again.json()["detail"]["error_code"] == "invalid_state_transition"
    assert f.row() == before


def test_mailer_failure_during_approval_approves_nothing(client, mailer, monkeypatch):
    f = Flow(client, mailer, monkeypatch, "a9")
    digest = f.to_draft()
    mailer.fail_next(503)
    r = f.approve(digest)
    assert r.status_code == 502 and f.row()["state"] == "pending_review" and f.row()["approved_content_sha256"] is None


@pytest.mark.parametrize("scripted", ["failed", "cancelled"])
def test_a_failed_or_cancelled_draft_cannot_be_approved(client, mailer, monkeypatch, scripted):
    f = Flow(client, mailer, monkeypatch, "a10" + scripted)
    f.start()
    f.mailer.fail_generation(f.tenant, f.key) if scripted == "failed" else f.mailer.set_dispatch_state(
        f.tenant, f.key, "cancelled")
    d = f.draft().json()
    assert d["state"] == scripted and d["body"] is None
    r = f.approve("a" * 64)
    assert r.status_code == 409 and f.row()["state"] == "pending_review"


# =============================================================== dispatch = release of the approved draft


def test_dispatching_an_unapproved_reply_is_refused_and_contacts_the_mailer_not_at_all(client, mailer, monkeypatch):
    f = Flow(client, mailer, monkeypatch, "r1")
    f.to_draft()
    before = len(mailer.calls)
    r = f.dispatch()
    assert r.status_code == 409 and r.json()["detail"]["error_code"] == "invalid_state_transition"
    assert len(mailer.calls) == before and f.row()["state"] == "pending_review"


def test_a_reply_with_no_approved_digest_cannot_be_dispatched_even_if_forced_to_approved(client, mailer, monkeypatch):
    """Defence in depth: APPROVED without evidence (e.g. a pre-feature row) is refused BEFORE the claim."""
    f = Flow(client, mailer, monkeypatch, "r2")
    f.to_draft()
    s = SessionLocal()
    try:
        s.get(OutreachAction, f.aid).state = "approved"
        s.commit()
    finally:
        s.close()
    before = len(mailer.calls)
    r = f.dispatch()
    assert r.status_code == 409 and r.json()["detail"]["error_code"] == "reply_approval_missing"
    assert f.row()["state"] == "approved", "refused before the atomic claim: nothing moved"
    assert len(mailer.calls) == before


def test_dispatch_releases_the_same_draft_with_the_approved_digest_and_does_not_regenerate(client, mailer, monkeypatch):
    f = Flow(client, mailer, monkeypatch, "r3")
    digest = f.to_approved()
    gen_calls = len(mailer.calls_to("POST", DISPATCH_PATH))
    r = f.dispatch()
    assert r.status_code == 200 and r.json()["state"] == "submitted"
    assert len(mailer.calls_to("POST", DISPATCH_PATH)) == gen_calls == 1, "no second generation request"
    (release,) = f.draft_calls()
    assert release.path == f"{DRAFTS}{f.key}/release" and release.body == {"content_sha256": digest}
    assert len(mailer.dispatches) == 1 and f.rec()["state"] == "queued" and f.rec()["approved"] == digest
    assert f.row()["submitted_at"] is not None and f.row()["last_dispatch_error"] is None


def test_dispatch_uses_the_approved_digest_not_whatever_the_draft_is_now(client, mailer, monkeypatch):
    f = Flow(client, mailer, monkeypatch, "r4")
    digest = f.to_approved()
    f.mailer.mutate_draft(f.tenant, f.key, body="Tampered after approval.")
    r = f.dispatch()
    assert r.status_code == 200 and r.json()["state"] == "dispatch_failed"
    assert r.json()["last_dispatch_error"] == "reply_release_draft_changed"
    (release,) = f.draft_calls()
    assert release.body == {"content_sha256": digest}, "the stored approval is what is sent -- never a re-fetched hash"
    assert f.rec()["state"] == "awaiting_approval" and f.rec()["approved"] is None, "nothing was released"


def test_a_second_dispatch_after_submission_is_refused_and_releases_nothing_again(client, mailer, monkeypatch):
    f = Flow(client, mailer, monkeypatch, "r5")
    f.to_approved()
    assert f.dispatch().status_code == 200
    calls = len(mailer.calls)
    r = f.dispatch()
    assert r.status_code == 409 and len(mailer.calls) == calls


def test_ambiguous_release_is_reconciled_by_repeating_it_and_never_double_sends(client, mailer, monkeypatch):
    """The release was applied but the response was lost. Retrying replays idempotently -> submitted."""
    f = Flow(client, mailer, monkeypatch, "r6")
    digest = f.to_approved()
    dropped = {"n": 0}

    def drop_release_response(call):
        if call.path.endswith("/release") and dropped["n"] == 0:
            dropped["n"] += 1
            raise httpx.ReadTimeout("release applied, response lost")

    mailer.after_call = drop_release_response
    r = f.dispatch()
    assert r.json()["state"] == "dispatch_failed" and r.json()["last_dispatch_error"] == "mailing_agent_timeout"
    assert f.rec()["state"] == "queued", "the Mailer did release it"

    r = f.dispatch()
    assert r.status_code == 200 and r.json()["state"] == "submitted" and r.json()["last_dispatch_error"] is None
    assert [c.body for c in f.draft_calls("/release")] == [{"content_sha256": digest}] * 2
    assert len(mailer.calls_to("POST", DISPATCH_PATH)) == 1 and len(mailer.dispatches) == 1


@pytest.mark.parametrize("fault", ["timeout", "connect", "503"])
def test_transport_failures_leave_a_retryable_dispatch_failed_state(client, mailer, monkeypatch, fault):
    f = Flow(client, mailer, monkeypatch, "r7" + fault)
    f.to_approved()
    {"timeout": mailer.timeout_next, "connect": mailer.connect_error_next, "503": mailer.fail_next}[fault]()
    r = f.dispatch()
    assert r.status_code == 200 and r.json()["state"] == "dispatch_failed"
    assert f.rec()["state"] == "awaiting_approval", "nothing was released by a failed call"
    assert f.dispatch().json()["state"] == "submitted", "the same call, retried, succeeds"


def test_org_pause_and_sender_gates_still_apply_to_a_reply_dispatch(client, mailer, monkeypatch):
    f = Flow(client, mailer, monkeypatch, "r8")
    f.to_approved()
    assert client.put(f"/api/v2/organizations/{f.org}/outreach-policy", headers=f.h,
                      json={"is_paused": True}).status_code == 200
    r = f.dispatch()
    assert r.json()["state"] == "dispatch_failed" and "paused" in r.json()["last_dispatch_error"].lower()
    assert f.draft_calls() == [], "a paused organization never reaches the Mailer"


# =============================================================== cancellation


def test_cancel_before_approval_revokes_the_held_draft(client, mailer, monkeypatch):
    f = Flow(client, mailer, monkeypatch, "x1")
    digest = f.to_draft()
    r = f.cancel()
    assert r.status_code == 200 and r.json()["state"] == "cancelled"
    assert f.rec()["state"] == "cancelled"
    assert f.approve(digest).status_code == 409
    assert f.dispatch().status_code == 409
    assert f.draft_calls("/release") == [], "a cancelled draft is never released"


def test_cancel_after_approval_revokes_the_draft_and_blocks_dispatch(client, mailer, monkeypatch):
    f = Flow(client, mailer, monkeypatch, "x2")
    f.to_approved()
    assert f.cancel().json()["state"] == "cancelled" and f.rec()["state"] == "cancelled"
    assert f.dispatch().status_code == 409 and f.draft_calls("/release") == []


def test_cancel_when_the_mailer_is_down_still_cancels_and_the_draft_is_unreleasable(client, mailer, monkeypatch):
    """LeadBoost never dispatches a CANCELLED action, so the un-revoked Mailer draft cannot be released."""
    f = Flow(client, mailer, monkeypatch, "x3")
    f.to_approved()
    mailer.connect_error_next()
    r = f.cancel()
    assert r.status_code == 200 and r.json()["state"] == "cancelled"
    assert f.rec()["state"] == "awaiting_approval", "the revocation did not land"
    assert f.dispatch().status_code == 409 and f.draft_calls("/release") == []


def test_cancel_before_generation_started_does_not_touch_the_mailer(client, mailer, monkeypatch):
    f = Flow(client, mailer, monkeypatch, "x4")
    assert f.cancel().json()["state"] == "cancelled"
    assert mailer.calls == [] or all(c.path.startswith("/mailboxes") for c in mailer.calls)


def test_second_cancel_is_a_409_and_changes_nothing(client, mailer, monkeypatch):
    f = Flow(client, mailer, monkeypatch, "x5")
    f.to_draft()
    f.cancel()
    before = (f.row(), json.dumps(f.rec(), sort_keys=True, default=str))
    r = f.cancel()
    assert r.status_code == 409 and (f.row(), json.dumps(f.rec(), sort_keys=True, default=str)) == before


def test_cancel_after_a_release_that_actually_succeeded_is_refused_not_misreported(client, mailer, monkeypatch):
    """The dangerous one: DISPATCH_FAILED looks cancellable, but the lost response hid a successful release. If
    LeadBoost said 'cancelled' the email would still go out. The Mailer is asked first and refuses."""
    f = Flow(client, mailer, monkeypatch, "x6")
    f.to_approved()
    mailer.after_call = lambda call: (_ for _ in ()).throw(httpx.ReadTimeout("lost")) if call.path.endswith("/release") else None
    assert f.dispatch().json()["state"] == "dispatch_failed"
    mailer.after_call = None
    assert f.rec()["state"] == "queued"

    r = f.cancel()
    assert r.status_code == 409 and "already released" in r.json()["detail"]["message"]
    assert f.row()["state"] == "dispatch_failed", "LeadBoost must not claim a cancel the Mailer refused"
    assert f.dispatch().json()["state"] == "submitted", "and the honest next step -- retry -- reconciles it"


def test_cancel_after_a_failed_release_that_did_not_land_revokes_it(client, mailer, monkeypatch):
    f = Flow(client, mailer, monkeypatch, "x7")
    f.to_approved()
    mailer.fail_next(503)
    assert f.dispatch().json()["state"] == "dispatch_failed"
    r = f.cancel()
    assert r.status_code == 200 and r.json()["state"] == "cancelled" and f.rec()["state"] == "cancelled"


def test_cancel_after_a_failed_release_with_the_mailer_unreachable_is_refused(client, mailer, monkeypatch):
    f = Flow(client, mailer, monkeypatch, "x8")
    f.to_approved()
    mailer.fail_next(503)
    f.dispatch()
    mailer.connect_error_next()
    r = f.cancel()
    assert r.status_code == 502 and r.json()["detail"]["error_code"] == "reply_draft_unavailable"
    assert f.row()["state"] == "dispatch_failed", "cannot confirm revocation -> do not claim cancelled"


# =============================================================== isolation and unchanged ordinary behaviour


def test_another_tenant_cannot_start_read_approve_dispatch_or_cancel(client, mailer, monkeypatch):
    f = Flow(client, mailer, monkeypatch, "t1")
    digest = f.to_draft()
    h2, org2, uid2, tenant2 = _env(client, mailer, monkeypatch, "t1b")
    before_row, calls = f.row(), len(mailer.calls)
    for method, path, kw in [
        ("post", f"{API}/{f.aid}/reply-draft", {}),
        ("get", f"{API}/{f.aid}/reply-draft", {}),
        ("post", f"{API}/{f.aid}/approve", {"json": {"content_sha256": digest}}),
        ("post", f"{API}/{f.aid}/dispatch", {}),
        ("post", f"{API}/{f.aid}/cancel", {}),
    ]:
        r = getattr(client, method)(path, headers=h2, **kw)
        assert r.status_code == 404, (method, path, r.status_code)
    assert f.row() == before_row and len(mailer.calls) == calls, "a foreign tenant causes no Mailer traffic at all"


def test_unauthenticated_requests_are_refused(client, mailer, monkeypatch):
    f = Flow(client, mailer, monkeypatch, "t2")
    for method, path in [("post", "reply-draft"), ("get", "reply-draft"), ("post", "approve")]:
        assert getattr(client, method)(f"{API}/{f.aid}/{path}").status_code in (401, 403)


def test_each_tenant_uses_its_own_mailer_key(client, mailer, monkeypatch):
    f = Flow(client, mailer, monkeypatch, "t3")
    f.to_draft()
    f.draft()
    assert {c.tenant for c in mailer.calls_to("GET", DRAFTS)} == {f.tenant}
    assert all(c.api_key == f"key-org-{f.org}" for c in mailer.calls_to("GET", DRAFTS))


def test_reply_draft_endpoints_refuse_an_ordinary_action(client, mailer, monkeypatch):
    h, org, uid, tenant = _env(client, mailer, monkeypatch, "o1")
    acc = _account(client, h, org, mailer)
    aid = _approved(client, h, _lead(org, uid), acc["id"])
    for method, path, kw in [("post", f"{API}/{aid}/reply-draft", {}), ("get", f"{API}/{aid}/reply-draft", {})]:
        r = getattr(client, method)(path, headers=h, **kw)
        assert r.status_code == 409 and r.json()["detail"]["error_code"] == "not_a_reply"
    assert mailer.calls_to("GET", DRAFTS) == []


def test_ordinary_outreach_is_byte_for_byte_the_old_flow(client, mailer, monkeypatch):
    """approve with NO body, dispatch -> exactly one generation request, zero reply-draft calls."""
    h, org, uid, tenant = _env(client, mailer, monkeypatch, "o2")
    acc = _account(client, h, org, mailer)
    aid = client.post(API, headers=h, json={"lead_id": _lead(org, uid), "email_account_id": acc["id"],
                                            "mode": "manual"}).json()["id"]
    r = client.post(f"{API}/{aid}/approve", headers=h)
    assert r.status_code == 200 and r.json()["state"] == "approved" and r.json()["approved_content_sha256"] is None
    r = _dispatch(client, h, aid)
    assert r.json()["state"] == "submitted"
    (call,) = mailer.calls_to("POST", DISPATCH_PATH)
    assert "in_reply_to" not in call.body
    assert mailer.calls_to("GET", DRAFTS) == [] and mailer.calls_to("POST", DRAFTS) == []


def test_ordinary_approval_ignores_any_body(client, mailer, monkeypatch):
    h, org, uid, tenant = _env(client, mailer, monkeypatch, "o3")
    acc = _account(client, h, org, mailer)
    aid = client.post(API, headers=h, json={"lead_id": _lead(org, uid), "email_account_id": acc["id"],
                                            "mode": "manual"}).json()["id"]
    r = client.post(f"{API}/{aid}/approve", headers=h, json={"content_sha256": "a" * 64})
    assert r.status_code == 200 and r.json()["approved_content_sha256"] is None, "ordinary approval is unchanged"


# =============================================================== start-draft: real read-back failures are not "generating"


def _route_draft_returning(mailer, *, only_get=True, status=200, payload=None):
    """Make the fake Mailer's reply-draft route answer with a fixed response (GET only by default)."""
    original = mailer._route_draft

    def route(call, t, b):
        if only_get and call.method != "GET":
            return original(call, t, b)
        return httpx.Response(status, json=payload if payload is not None else {})

    mailer._route_draft = route
    return original


@pytest.mark.parametrize("scenario, status, payload, expected_status, expected_code", [
    ("mailer outage", 503, {"detail": "boom"}, 502, "reply_draft_unavailable"),
    ("schema drift", 200, {"state": "awaiting_approval"}, 502, "reply_draft_unavailable"),
    ("unknown field", 200, {"state": "generating", "mailbox_id": 7, "updated_at": None}, 502, "reply_draft_unavailable"),
    ("key rejected", 401, {"detail": "no"}, 502, "reply_draft_unavailable"),
    ("draft too large", 422, {"detail": "draft_too_large"}, 422, "reply_draft_too_large"),
])
def test_start_draft_reports_a_real_readback_failure_instead_of_generating(
    client, mailer, monkeypatch, scenario, status, payload, expected_status, expected_code
):
    f = Flow(client, mailer, monkeypatch, "sd-" + scenario.replace(" ", "-"))
    original = _route_draft_returning(mailer, status=status, payload=payload)
    r = f.start()
    assert r.status_code == expected_status, (scenario, r.text)
    assert r.json()["detail"]["error_code"] == expected_code
    assert "generating" not in r.text, "a real failure must never be disguised as progress"
    assert "boom" not in r.text and "mailbox_id" not in r.text, "no raw Mailer text reaches the browser"
    # The request WAS accepted and is recorded; nothing was duplicated, and repeating is idempotent and recovers.
    assert f.row()["reply_draft_requested_at"] is not None
    assert len(mailer.dispatches) == 1
    mailer._route_draft = original
    again = f.start()
    assert again.status_code == 200 and again.json()["state"] == "generating"
    assert len(mailer.dispatches) == 1


def test_start_draft_still_returns_generating_when_the_mailer_has_no_draft_yet(client, mailer, monkeypatch):
    """The ONE read-back outcome that legitimately means "still being written": the Mailer has nothing to show yet."""
    f = Flow(client, mailer, monkeypatch, "sd-notready")
    _route_draft_returning(mailer, status=404, payload={"detail": "Reply draft not found"})
    r = f.start()
    assert r.status_code == 200 and r.json()["state"] == "generating"
    assert f.row()["reply_draft_requested_at"] is not None


def test_a_draft_too_large_to_review_can_be_neither_read_nor_approved(client, mailer, monkeypatch):
    f = Flow(client, mailer, monkeypatch, "sd-toolarge")
    digest = f.to_draft()
    _route_draft_returning(mailer, status=422, payload={"detail": "draft_too_large"})
    r = f.draft()
    assert r.status_code == 422 and r.json()["detail"]["error_code"] == "reply_draft_too_large"
    r = f.approve(digest)
    assert r.status_code == 422 and r.json()["detail"]["error_code"] == "reply_draft_too_large"
    assert f.row()["state"] == "pending_review" and f.row()["approved_content_sha256"] is None


# =============================================================== reply actions that predate exact-content approval (C9.4)
#
# A C9.4 reply was approved as an ACTION and its text was written by the Mailer afterwards. These tests pin how each LeadBoost
# state is handled by the new code. The rule: an old approval is NEVER treated as approval of a new draft; nothing is reset,
# deleted or silently reused; the only exit from the stuck states is the existing, explicit Cancel.


def _force(f, **fields):
    """Put the action into a C9.4-era shape (no draft requested, no approved digest) by writing the row directly."""
    s = SessionLocal()
    try:
        a = s.get(OutreachAction, f.aid)
        for k, v in fields.items():
            setattr(a, k, v)
        s.commit()
    finally:
        s.close()


def _legacy(f, state, **extra):
    base = dict(state=state, reply_draft_requested_at=None, approved_content_sha256=None, approved_draft_version=None)
    if state in ("approved", "dispatching", "dispatch_failed", "submitted"):
        base.update(approved_by_user_id=_owner(f.org), approved_at=datetime.now(timezone.utc))
    if state in ("dispatching", "dispatch_failed", "submitted"):
        base["dispatch_attempts"] = 1
    if state == "dispatch_failed":
        base["last_dispatch_error"] = "mailing_agent_timeout"
    if state == "submitted":
        base.update(submitted_at=datetime.now(timezone.utc), mailing_agent_reference="dsp_legacy")
    base.update(extra)
    _force(f, **base)
    return f.row()


@pytest.mark.parametrize("state", ["approved", "dispatching", "dispatch_failed", "submitted"])
def test_a_legacy_approval_is_never_reused_for_a_new_draft(client, mailer, monkeypatch, state):
    f = Flow(client, mailer, monkeypatch, "lg-" + state)
    before = _legacy(f, state)
    calls = len(mailer.calls)

    # It cannot be started, re-approved (with ANY digest) or sent -- and none of those refusals touches the Mailer.
    r = f.start()
    assert r.status_code == 409 and "before exact-content review" in r.json()["detail"]["message"]
    r = f.approve(fake_content_digest("Re: Hello", "any text at all"))
    assert r.status_code == 409 and "before exact-content review" in r.json()["detail"]["message"]
    r = f.dispatch()
    assert r.status_code == 409
    if state in ("approved", "dispatch_failed"):          # the states dispatch would otherwise accept
        assert r.json()["detail"]["error_code"] == "reply_approval_missing"
        assert "cannot be sent" in r.json()["detail"]["message"]

    d = f.draft()
    assert d.status_code == 200 and d.json()["state"] == "legacy"
    assert d.json()["subject"] is None and d.json()["body"] is None and d.json()["content_sha256"] is None
    assert len(mailer.calls) == calls, "refusing a legacy action must not contact the Mailer"
    after = f.row()
    assert after == before, "nothing about a legacy action is rewritten, reset or 'upgraded'"
    assert after["approved_content_sha256"] is None


def test_a_legacy_approved_reply_can_only_be_cancelled_and_that_is_terminal_for_its_message(client, mailer, monkeypatch):
    f = Flow(client, mailer, monkeypatch, "lg-cancel-approved")
    _legacy(f, "approved")                                 # approved as an action; nothing was ever dispatched
    calls = len(mailer.calls)
    r = f.cancel()
    assert r.status_code == 200 and r.json()["state"] == "cancelled"
    assert len(mailer.calls) == calls, "nothing was dispatched, so there is no Mailer operation to revoke"
    # Documented v1 limit: replying to that inbound message again returns the same cancelled action.
    again = _reply(client, f.h, f.lead, f.acc, REF)
    assert again.status_code == 201 and again.json()["id"] == f.aid and again.json()["state"] == "cancelled"
    assert f.start().status_code == 409


def test_cancelling_a_legacy_failed_dispatch_revokes_a_held_draft_at_the_mailer_first(client, mailer, monkeypatch):
    """An old LeadBoost run against the NEW Mailer left a held draft behind. Cancel must revoke it, not just hide it."""
    f = Flow(client, mailer, monkeypatch, "lg-held")
    f.to_draft()                                           # the Mailer now holds a draft under this action's key
    _legacy(f, "dispatch_failed")
    assert f.rec()["state"] == "awaiting_approval"
    r = f.cancel()
    assert r.status_code == 200 and r.json()["state"] == "cancelled"
    assert f.rec()["state"] == "cancelled"


def test_cancelling_a_legacy_failed_dispatch_is_refused_when_the_mailer_already_released_it(client, mailer, monkeypatch):
    f = Flow(client, mailer, monkeypatch, "lg-released")
    f.to_draft()
    _legacy(f, "dispatch_failed")
    f.rec()["state"], f.rec()["approved"] = "queued", "f" * 64    # released at the Mailer
    r = f.cancel()
    assert r.status_code == 409 and "already released" in r.json()["detail"]["message"]
    assert f.row()["state"] == "dispatch_failed", "never claim a cancel the Mailer refused"


def test_cancelling_a_legacy_failed_dispatch_is_refused_when_the_mailer_cannot_confirm(client, mailer, monkeypatch):
    f = Flow(client, mailer, monkeypatch, "lg-down")
    f.to_draft()
    _legacy(f, "dispatch_failed")
    mailer.connect_error_next()
    r = f.cancel()
    assert r.status_code == 502 and r.json()["detail"]["error_code"] == "reply_draft_unavailable"
    assert f.row()["state"] == "dispatch_failed"


def test_cancelling_a_legacy_failed_dispatch_with_no_mailer_operation_just_cancels(client, mailer, monkeypatch):
    f = Flow(client, mailer, monkeypatch, "lg-none")
    _legacy(f, "dispatch_failed")                          # the old generation request never reached the Mailer
    r = f.cancel()
    assert r.status_code == 200 and r.json()["state"] == "cancelled"


# ---- legacy C9.4 operation that EXISTS at the Mailer (flag false, reply_parent present) ----------------------------
# The Mailer answers 409 `legacy_reply_requires_reconciliation` for it -- never 404 -- and LeadBoost must not mark its
# action CANCELLED unless the Mailer proves nothing was or can be sent. (404 stays "the Mailer has no such operation".)

_LEGACY_AT_MAILER = [
    ("sent", True), ("unknown", True), ("sending", True), ("queued", True), ("queued", False),
]


@pytest.mark.parametrize("mailer_state, has_message", _LEGACY_AT_MAILER,
                         ids=["sent", "unknown", "sending", "queued-with-message", "queued-without-message"])
def test_cancelling_a_legacy_dispatch_that_exists_at_the_mailer_is_refused_and_changes_nothing(
        client, mailer, monkeypatch, mailer_state, has_message):
    f = Flow(client, mailer, monkeypatch, f"lg-ex-{mailer_state}-{int(has_message)}")
    mailer.make_legacy_dispatch(f.tenant, f.key, mailer_state, message=has_message)
    before = _legacy(f, "dispatch_failed")             # the old dispatch result was ambiguous: LeadBoost saw a failure
    rec_before = dict(f.rec())

    r = f.cancel()

    assert r.status_code == 409, r.text
    detail = r.json()["detail"]
    assert detail["error_code"] == "invalid_state_transition" and "reconcile" in detail["message"]
    assert f.row() == before, "LeadBoost must keep its prior state, history and approval snapshot on a conflict"
    assert f.row()["state"] == "dispatch_failed"
    assert f.rec() == rec_before, "and the Mailer's operation is untouched"
    # the Mailer WAS asked, exactly once, before anything local happened
    assert len(mailer.calls_to("POST", DRAFTS + f.key + "/cancel")) == 1


@pytest.mark.parametrize("mailer_state", ["failed", "cancelled"])
def test_cancelling_a_legacy_dispatch_the_mailer_proves_was_never_sent_cancels(client, mailer, monkeypatch, mailer_state):
    f = Flow(client, mailer, monkeypatch, "lg-proof-" + mailer_state)
    mailer.make_legacy_dispatch(f.tenant, f.key, mailer_state)
    _legacy(f, "dispatch_failed")
    r = f.cancel()
    assert r.status_code == 200 and r.json()["state"] == "cancelled"


def test_a_dispatched_legacy_approved_action_also_asks_the_mailer_first(client, mailer, monkeypatch):
    """APPROVED with a recorded dispatch attempt is not a normal C9.4 shape, but if it ever occurs LeadBoost must
    not cancel locally first and merely log a refusal afterwards."""
    f = Flow(client, mailer, monkeypatch, "lg-appr-attempt")
    mailer.make_legacy_dispatch(f.tenant, f.key, "sending")
    before = _legacy(f, "approved", dispatch_attempts=1)
    r = f.cancel()
    assert r.status_code == 409 and f.row() == before


def test_an_ok_cancel_answer_that_is_not_a_revocation_is_not_trusted(client, mailer, monkeypatch):
    """Defence in depth: only 'cancelled' / 'failed' proves nothing can be sent."""
    f = Flow(client, mailer, monkeypatch, "lg-ok-sent")
    _legacy(f, "dispatch_failed")
    original = mailer._route_draft

    def route(call, t, b):
        if call.method == "POST" and call.path.endswith("/cancel"):
            return httpx.Response(200, json=dict(state="sent", subject="s", body="b", content_sha256="a" * 64,
                                                 draft_version=1, updated_at=None))
        return original(call, t, b)

    mailer._route_draft = route
    before = f.row()
    r = f.cancel()
    assert r.status_code == 409 and f.row() == before


def test_the_client_keeps_the_legacy_conflict_distinct_from_not_found_and_from_a_generic_conflict(
        client, mailer, monkeypatch):
    import asyncio
    f = Flow(client, mailer, monkeypatch, "lg-client")
    mailer.make_legacy_dispatch(f.tenant, f.key, "sent")
    res = asyncio.run(mc_drafts.cancel_reply_draft(organization_id=f.org, idempotency_key=f.key))
    assert res.status == "conflict" and res.conflict_code == "legacy_reply_requires_reconciliation"
    missing = asyncio.run(mc_drafts.cancel_reply_draft(organization_id=f.org, idempotency_key="never-dispatched"))
    assert missing.status == "not_found"


def test_a_genuinely_nonexistent_mailer_operation_still_lets_a_legacy_failed_dispatch_cancel(client, mailer, monkeypatch):
    f = Flow(client, mailer, monkeypatch, "lg-nonexistent")
    _legacy(f, "dispatch_failed")                       # no operation under this key at the Mailer: a real 404
    assert (f.tenant, f.key) not in mailer.dispatches
    r = f.cancel()
    assert r.status_code == 200 and r.json()["state"] == "cancelled"


@pytest.mark.parametrize("state", ["dispatching", "submitted"])
def test_a_legacy_reply_that_left_leadboost_cannot_be_cancelled(client, mailer, monkeypatch, state):
    f = Flow(client, mailer, monkeypatch, "lg-nocancel-" + state)
    _legacy(f, state)
    calls = len(mailer.calls)
    r = f.cancel()
    assert r.status_code == 409 and r.json()["detail"]["error_code"] == "invalid_state_transition"
    assert len(mailer.calls) == calls


def test_a_c94_reply_still_pending_review_takes_the_new_flow_and_its_old_snapshot_is_never_the_draft(client, mailer, monkeypatch):
    """PENDING_REVIEW is not 'legacy': it approved nothing. It simply takes the new flow, and the outreach text that C9.4 stored
    on it (an unrelated snapshot) is neither shown as the reply nor sent."""
    f = Flow(client, mailer, monkeypatch, "lg-pending")
    _force(f, subject="Old outreach subject", body="OLD OUTREACH SNAPSHOT")
    digest = f.to_approved()
    d = f.draft().json()
    assert d["body"] != "OLD OUTREACH SNAPSHOT" and d["subject"] != "Old outreach subject"
    r = f.dispatch()
    assert r.status_code == 200 and r.json()["state"] == "submitted"
    assert f.row()["approved_content_sha256"] == digest
    (release,) = f.draft_calls("/release")
    assert "OLD OUTREACH" not in json.dumps(release.body) and release.body == {"content_sha256": digest}
