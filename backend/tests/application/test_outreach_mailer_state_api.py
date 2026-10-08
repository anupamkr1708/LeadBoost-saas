"""
C9.3 -- GET /api/v2/outreach-actions/{action_id}/mailer-state.

The whole customer path, for real: authenticated browser-facing LeadBoost API ->
real outreach_mailer_state service -> real server-side Mailer client/transport ->
an in-memory Mailer (tests/application/fake_mailer.py) -> a safe product response.

Invariants proven here:
  * the Mailer is the source of truth and LeadBoost only displays it -- the
    OutreachAction is NEVER written (not its state, not DISPATCH_FAILED "healing");
  * org-scoped: another organization's action is a 404 and costs no Mailer call;
  * Mailer-side problems are availability/error_code with HTTP 200 -- never a false
    success, never raw error text;
  * nothing Mailer-internal (URL, key, tenant, references, paths) reaches the browser;
  * the read path carries no credential and no request body.
"""

import ast
import json
import os
import re
import uuid
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, update

import main
from core.domain.models.outreach_action import OutreachAction, OutreachState
from core.infrastructure.database import SessionLocal, engine
from core.infrastructure.mailing_agent import client as mc
from core.infrastructure.mailing_agent.conversation_client import MAILER_STATE_ERROR_CODES
from tests.application.fake_mailer import FakeMailer
from tests.application.test_outreach_dispatch_l1 import _account, _approved, _dispatch, _env, _lead

CONV = "/integrations/leadboost/outreach-actions/"
TOP_KEYS = {"availability", "error_code", "mailer"}
MAILER_KEYS = {"delivery_state", "updated_at", "messages", "has_more"}
MESSAGE_KEYS = {"direction", "subject", "body", "body_truncated", "created_at", "delivery_state"}


@pytest.fixture()
def client():
    with TestClient(main.app) as c:
        yield c


@pytest.fixture()
def mailer(monkeypatch):
    monkeypatch.delenv("MAILING_AGENT_ORG_API_KEYS", raising=False)
    return FakeMailer().install(monkeypatch, mc)


class Ctx:
    def __init__(self, h, org, tenant, aid, key):
        self.h, self.org, self.tenant, self.aid, self.key = h, org, tenant, aid, key

    @property
    def url(self):
        return f"/api/v2/outreach-actions/{self.aid}/mailer-state"


def _key_of(aid):
    s = SessionLocal()
    try:
        return s.get(OutreachAction, aid).idempotency_key
    finally:
        s.close()


def _action_rows():
    s = SessionLocal()
    try:
        return [
            {c.name: getattr(r, c.name) for c in OutreachAction.__table__.columns}
            for r in s.query(OutreachAction).order_by(OutreachAction.id).all()
        ]
    finally:
        s.close()


def _make(client, mailer, monkeypatch, tag, *, dispatch=True, key=None) -> Ctx:
    h, org, uid, tenant = _env(client, mailer, monkeypatch, tag)
    acc = _account(client, h, org, mailer)
    lead_id = _lead(org, uid)
    if key is None:
        aid = _approved(client, h, lead_id, acc["id"])
    else:
        r = client.post(
            "/api/v2/outreach-actions",
            headers=h,
            json={
                "lead_id": lead_id,
                "email_account_id": acc["id"],
                "mode": "manual",
                "idempotency_key": key,
            },
        )
        assert r.status_code in (200, 201), r.text
        aid = r.json()["id"]
        assert client.post(f"/api/v2/outreach-actions/{aid}/approve", headers=h).status_code == 200
    if dispatch:
        assert _dispatch(client, h, aid).status_code == 200
    return Ctx(h, org, tenant, aid, _key_of(aid))


def _get(client, ctx):
    return client.get(ctx.url, headers=ctx.h)


def _read_calls(mailer):
    return [c for c in mailer.calls if c.method == "GET" and c.path.startswith(CONV)]


# --------------------------------------------------------------------------- golden path
def test_dashboard_shows_the_mailers_state_and_the_actual_conversation(client, mailer, monkeypatch):
    ctx = _make(client, mailer, monkeypatch, "golden")
    mailer.set_dispatch_state(ctx.tenant, ctx.key, "sent")
    mailer.add_message(
        ctx.tenant,
        ctx.key,
        direction="outbound",
        subject="A better subject than the snapshot",
        body="The ACTUAL generated email the Mailer sent.",
        created_at="2026-01-01T12:00:00Z",
    )
    mailer.add_message(
        ctx.tenant,
        ctx.key,
        direction="inbound",
        subject="Re: A better subject",
        body="Thanks -- tell me more.",
        created_at="2026-01-01T13:00:00Z",
    )
    mailer.calls.clear()

    r = _get(client, ctx)

    assert r.status_code == 200
    body = r.json()
    assert set(body) == TOP_KEYS
    assert body["availability"] == "available" and body["error_code"] is None
    m = body["mailer"]
    assert set(m) == MAILER_KEYS and m["delivery_state"] == "sent" and m["has_more"] is False
    assert [x["direction"] for x in m["messages"]] == ["outbound", "inbound"]
    assert all(set(x) == MESSAGE_KEYS for x in m["messages"])
    assert m["messages"][0]["body"] == "The ACTUAL generated email the Mailer sent."
    assert m["messages"][0]["delivery_state"] == "sent" and m["messages"][1]["delivery_state"] is None
    # LeadBoost's own snapshot is NOT what the dashboard shows as the delivered message
    assert "Hi Jamie, quick note" not in r.text
    # ...and exactly one server-side GET went to the Mailer, with this org's key
    (call,) = mailer.calls
    assert (call.method, call.api_key, call.tenant) == ("GET", f"key-org-{ctx.org}", ctx.tenant)
    assert call.raw == b"" and call.raw_target == f"{CONV}{quote(ctx.key, safe='')}/conversation?limit=20"
    assert r.headers["cache-control"] == "no-store"


def test_leadboost_and_mailer_state_are_shown_side_by_side_never_merged(client, mailer, monkeypatch):
    ctx = _make(client, mailer, monkeypatch, "sidebyside")
    mailer.set_dispatch_state(ctx.tenant, ctx.key, "sent")
    oa = client.get(f"/api/v2/outreach-actions/{ctx.aid}", headers=ctx.h).json()
    ms = _get(client, ctx).json()
    assert oa["state"] == OutreachState.SUBMITTED  # LeadBoost: "Submitted" (handoff)
    assert ms["mailer"]["delivery_state"] == "sent"  # Mailer: "Sent" (delivery)


# --------------------------------------------------------------------------- D5: display, never heal
def test_dispatch_failed_at_leadboost_but_sent_at_mailer_is_displayed_and_never_healed(
    client, mailer, monkeypatch
):
    ctx = _make(client, mailer, monkeypatch, "d5")
    mailer.set_dispatch_state(ctx.tenant, ctx.key, "sent")
    mailer.add_message(ctx.tenant, ctx.key, direction="outbound", body="It really did go out.")
    s = SessionLocal()  # the lost-response case: LeadBoost recorded a failure
    try:
        s.execute(
            update(OutreachAction)
            .where(OutreachAction.id == ctx.aid)
            .values(
                state=OutreachState.DISPATCH_FAILED,
                last_dispatch_error="mailing_agent_timeout",
                mailing_agent_reference=None,
            )
        )
        s.commit()
    finally:
        s.close()
    before = _action_rows()

    r = _get(client, ctx)

    assert r.json()["availability"] == "available" and r.json()["mailer"]["delivery_state"] == "sent"
    assert _action_rows() == before  # not one column of the action changed
    oa = client.get(f"/api/v2/outreach-actions/{ctx.aid}", headers=ctx.h).json()
    assert (
        oa["state"] == OutreachState.DISPATCH_FAILED and oa["last_dispatch_error"] == "mailing_agent_timeout"
    )
    assert oa["mailing_agent_reference"] is None


@pytest.mark.parametrize("state", ["queued", "sending", "sent", "failed", "unknown"])
def test_every_mailer_state_is_passed_through_and_unknown_is_not_translated(
    client, mailer, monkeypatch, state
):
    ctx = _make(client, mailer, monkeypatch, f"st{state}")
    mailer.set_dispatch_state(ctx.tenant, ctx.key, state)
    mailer.add_message(ctx.tenant, ctx.key, direction="outbound", body="b")
    m = _get(client, ctx).json()["mailer"]
    assert m["delivery_state"] == state and m["messages"][0]["delivery_state"] == state


# --------------------------------------------------------------------------- availability
def test_never_dispatched_action_is_not_dispatched_and_the_mailer_is_not_called(client, mailer, monkeypatch):
    ctx = _make(client, mailer, monkeypatch, "nodispatch", dispatch=False)
    mailer.calls.clear()
    r = _get(client, ctx)
    assert r.status_code == 200
    assert r.json() == {"availability": "not_dispatched", "error_code": None, "mailer": None}
    assert mailer.calls == []  # no needless Mailer call


def test_mailer_404_is_not_found_at_mailer_and_is_not_a_local_success(client, mailer, monkeypatch):
    ctx = _make(client, mailer, monkeypatch, "nf", dispatch=False)
    mailer.fail_next(503)  # the dispatch attempt fails: attempts=1, Mailer never recorded it
    assert _dispatch(client, ctx.h, ctx.aid).status_code == 200
    assert mailer.dispatches == {}
    before = _action_rows()
    r = _get(client, ctx)
    assert r.status_code == 200
    assert r.json() == {"availability": "not_found_at_mailer", "error_code": None, "mailer": None}
    assert _action_rows() == before
    assert (
        client.get(f"/api/v2/outreach-actions/{ctx.aid}", headers=ctx.h).json()["state"]
        == OutreachState.DISPATCH_FAILED
    )


@pytest.mark.parametrize(
    "inject,code",
    [
        (lambda m: m.fail_next(503), "mailing_agent_server_error"),
        (lambda m: m.fail_next(500), "mailing_agent_server_error"),
        (lambda m: m.fail_next(401), "mailing_agent_rejected"),
        (lambda m: m.timeout_next(), "mailing_agent_timeout"),
        (lambda m: m.connect_error_next(), "mailing_agent_unreachable"),
    ],
)
def test_mailer_failures_are_mailer_unavailable_with_a_closed_code_and_no_raw_text(
    client, mailer, monkeypatch, inject, code
):
    ctx = _make(client, mailer, monkeypatch, "fail" + code[-8:])
    before = _action_rows()
    inject(mailer)
    r = _get(client, ctx)
    assert r.status_code == 200
    assert r.json() == {"availability": "mailer_unavailable", "error_code": code, "mailer": None}
    assert code in MAILER_STATE_ERROR_CODES
    for raw in ("injected", "fake mailer", "timeout", "Traceback", "mailer.test"):
        assert raw not in r.text.replace(code, "")
    assert _action_rows() == before


def test_invalid_mailer_schema_is_mailer_unavailable(client, mailer, monkeypatch):
    ctx = _make(client, mailer, monkeypatch, "badschema")
    mailer.set_dispatch_state(ctx.tenant, ctx.key, "generating")  # not in the public vocabulary
    r = _get(client, ctx)
    assert r.json() == {
        "availability": "mailer_unavailable",
        "error_code": "mailing_agent_invalid_response",
        "mailer": None,
    }


def test_mailer_not_configured_or_unmapped_org_is_mailer_unavailable_not_a_crash(client, mailer, monkeypatch):
    ctx = _make(client, mailer, monkeypatch, "noconf")
    monkeypatch.setenv("MAILING_AGENT_ORG_API_KEYS", json.dumps({"99999": "someone-else"}))
    assert _get(client, ctx).json() == {
        "availability": "mailer_unavailable",
        "error_code": "mailing_agent_auth_not_configured",
        "mailer": None,
    }
    monkeypatch.delenv("MAILING_AGENT_BASE_URL")
    assert _get(client, ctx).json()["error_code"] == "mailing_agent_not_configured"


# --------------------------------------------------------------------------- tenancy / auth
def test_another_organizations_action_is_404_and_costs_no_mailer_call(client, mailer, monkeypatch):
    a = _make(client, mailer, monkeypatch, "orga")
    b = _make(client, mailer, monkeypatch, "orgb")
    mailer.set_dispatch_state(a.tenant, a.key, "sent")
    mailer.add_message(a.tenant, a.key, direction="outbound", body="ORG-A-PRIVATE-TEXT")
    mailer.calls.clear()

    cross = client.get(a.url, headers=b.h)  # org B asks for org A's action id
    missing = client.get("/api/v2/outreach-actions/99999999/mailer-state", headers=b.h)

    assert cross.status_code == missing.status_code == 404
    assert cross.json() == missing.json()
    assert "ORG-A-PRIVATE-TEXT" not in cross.text and a.key not in cross.text
    assert mailer.calls == []


def test_unauthenticated_and_garbage_tokens_are_rejected_before_anything_else(client, mailer, monkeypatch):
    ctx = _make(client, mailer, monkeypatch, "auth")
    mailer.calls.clear()
    assert client.get(ctx.url).status_code in (401, 403)
    assert client.get(ctx.url, headers={"Authorization": "Bearer garbage"}).status_code in (401, 403)
    assert mailer.calls == []


def test_each_organization_uses_its_own_mailer_key_and_sees_only_its_own_conversation(
    client, mailer, monkeypatch
):
    a = _make(client, mailer, monkeypatch, "ka")
    b = _make(client, mailer, monkeypatch, "kb")
    for ctx, text in ((a, "ONLY-A"), (b, "ONLY-B")):
        mailer.set_dispatch_state(ctx.tenant, ctx.key, "sent")
        mailer.add_message(ctx.tenant, ctx.key, direction="outbound", body=text)
    mailer.calls.clear()
    ra, rb = _get(client, a), _get(client, b)
    assert "ONLY-A" in ra.text and "ONLY-B" not in ra.text
    assert "ONLY-B" in rb.text and "ONLY-A" not in rb.text
    assert [c.api_key for c in mailer.calls] == [f"key-org-{a.org}", f"key-org-{b.org}"]


# --------------------------------------------------------------------------- nothing internal reaches the browser
def test_no_mailer_internals_credentials_or_schema_reach_the_browser(client, mailer, monkeypatch):
    ctx = _make(client, mailer, monkeypatch, "leak")
    rec = mailer.dispatch_record(ctx.tenant, ctx.key)
    mailer.set_dispatch_state(ctx.tenant, ctx.key, "sent")
    mailer.add_message(ctx.tenant, ctx.key, direction="outbound", body="hello")
    mailer.add_message(ctx.tenant, ctx.key, direction="inbound", body="reply")
    r = _get(client, ctx)
    lowered = r.text.lower()
    for internal in (
        "mailer.test",
        "https://",
        f"key-org-{ctx.org}",
        ctx.tenant,
        rec["reference"],
        rec["mailbox"],
        "x-api-key",
        "/integrations",
        "outreach-actions/",
        "mailing_agent_reference",
        "mailbox_reference",
        "message_type",
        "accepted",
        "idempotency",
    ):
        assert internal.lower() not in lowered, internal
    assert set(r.json()["mailer"]["messages"][0]) == MESSAGE_KEYS  # exactly the product fields, nothing more


def test_read_path_carries_no_credential_no_body_and_no_non_get_call(client, mailer, monkeypatch):
    ctx = _make(client, mailer, monkeypatch, "cred")
    mailer.calls.clear()
    _get(client, ctx)
    assert [c.method for c in mailer.calls] == ["GET"]
    assert mailer.credential_bearing_calls() == []
    (call,) = mailer.calls
    assert call.raw == b"" and "content-type" not in call.headers and "authorization" not in call.headers


def test_inbound_markup_is_returned_as_inert_json_text_for_plain_text_rendering(client, mailer, monkeypatch):
    ctx = _make(client, mailer, monkeypatch, "xss")
    evil = "<script>alert(1)</script><img src=x onerror=alert(2)>"
    mailer.set_dispatch_state(ctx.tenant, ctx.key, "sent")
    mailer.add_message(ctx.tenant, ctx.key, direction="inbound", body=evil, subject="<b>x</b>")
    # (the Fake's outbound/inbound mix only matters for delivery_state; this one is inbound-only)
    r = _get(client, ctx)
    assert r.headers["content-type"].startswith("application/json")
    m = r.json()["mailer"]["messages"][0]
    assert m["body"] == evil and m["subject"] == "<b>x</b>"  # data, byte-for-byte; the UI renders it as text


# --------------------------------------------------------------------------- window / keys
def test_default_window_is_20_and_has_more_is_passed_through(client, mailer, monkeypatch):
    ctx = _make(client, mailer, monkeypatch, "window")
    mailer.set_dispatch_state(ctx.tenant, ctx.key, "sent")
    for i in range(25):
        mailer.add_message(ctx.tenant, ctx.key, direction="inbound", body=f"m{i:02d}")
    mailer.calls.clear()
    m = _get(client, ctx).json()["mailer"]
    assert len(m["messages"]) == 20 and m["has_more"] is True
    assert [x["body"] for x in m["messages"]] == [f"m{i:02d}" for i in range(5, 25)]
    assert mailer.calls[0].raw_target.endswith("/conversation?limit=20")


@pytest.mark.parametrize("key", ["tenant/one two", "a?b#c%d", "naïve-ключ"])
def test_unusual_idempotency_keys_round_trip_encoded(client, mailer, monkeypatch, key):
    ctx = _make(client, mailer, monkeypatch, "key", key=key)
    assert ctx.key == key
    mailer.set_dispatch_state(ctx.tenant, key, "sent")
    mailer.add_message(ctx.tenant, key, direction="outbound", body="found it")
    mailer.calls.clear()
    r = _get(client, ctx)
    assert r.json()["availability"] == "available" and r.json()["mailer"]["messages"][0]["body"] == "found it"
    target = mailer.calls[0].raw_target
    assert re.fullmatch(
        r"/integrations/leadboost/outreach-actions/[A-Za-z0-9%._~-]+/conversation\?limit=20", target
    ), target


# --------------------------------------------------------------------------- read-only proof
def test_the_whole_read_issues_no_write_statements_and_changes_nothing(client, mailer, monkeypatch):
    ctx = _make(client, mailer, monkeypatch, "ro")
    mailer.set_dispatch_state(ctx.tenant, ctx.key, "sent")
    mailer.add_message(ctx.tenant, ctx.key, direction="outbound", body="x")
    before = _action_rows()
    statements = []

    def spy(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", spy)
    try:
        for _ in range(3):
            assert _get(client, ctx).status_code == 200
    finally:
        event.remove(engine, "before_cursor_execute", spy)

    assert statements
    writes = [
        s for s in statements if re.match(r"\s*(INSERT|UPDATE|DELETE|CREATE|ALTER|DROP|REPLACE)\b", s, re.I)
    ]
    assert writes == []
    assert _action_rows() == before


# --------------------------------------------------------------------------- architecture guards
SERVICE = os.path.join(
    os.path.dirname(__file__), "..", "..", "application", "services", "outreach_mailer_state.py"
)
FRONTEND_SRC = os.path.join(os.path.dirname(__file__), "..", "..", "..", "frontend-react", "src")


def test_service_is_structurally_read_only():
    tree = ast.parse(open(SERVICE).read())
    attrs = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    assert not attrs & {
        "add",
        "add_all",
        "commit",
        "flush",
        "delete",
        "merge",
        "execute",
        "bulk_save_objects",
    }
    imported = {
        a.name
        for n in ast.walk(tree)
        if isinstance(n, ast.ImportFrom) and n.module and n.module.endswith("crud")
        for a in n.names
    }
    assert imported == {"get_outreach_action"}  # one org-scoped read; no update_/claim_ helper
    modules = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
    assert "core.infrastructure.mailing_agent.client" not in modules  # no raw transport / dispatch from here


def test_the_browser_side_never_references_the_mailer():
    """The hard invariant: React talks only to LeadBoost. Nothing under the frontend
    source may name the Mailer's URL/env/key map or its internal paths."""
    # The pre-existing opaque `mailing_agent_reference` field on the OutreachAction type is a
    # reference, not a URL/key/path, so the field name itself is deliberately not banned.
    banned = (
        "MAILING_AGENT_BASE_URL",
        "MAILING_AGENT_ORG_API_KEYS",
        "MAILING_AGENT_TIMEOUT",
        "MAILER_URL",
        "MAILER_BASE_URL",
        "ORG_API_KEYS",
        "X-API-Key",
        "/integrations/leadboost",
        "mailer_agent",
        "NEXT_PUBLIC_MAILER",
    )
    offenders = []
    for root, _dirs, files in os.walk(FRONTEND_SRC):
        for f in files:
            if f.endswith((".ts", ".tsx", ".js", ".jsx", ".json", ".css")):
                path = os.path.join(root, f)
                text = open(path, encoding="utf-8", errors="ignore").read()
                offenders += [
                    (os.path.relpath(path, FRONTEND_SRC), b) for b in banned if b.lower() in text.lower()
                ]
    assert offenders == []


def _code_only(source: str) -> str:
    """TSX with comments removed (whole-line // comments, /* */ blocks and {/* */} JSX comments), so
    a guard inspects what executes -- not a comment that names the very thing it forbids."""
    source = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
    return "\n".join(line for line in source.splitlines() if not line.lstrip().startswith("//"))


def test_the_mailer_state_panel_can_only_render_message_text_as_plain_text():
    """Inbound mail is attacker-controlled. The panel must render it as React text nodes only:
    no raw-HTML injection and no HTML/markdown rendering library, anywhere in the outreach UI."""
    panel_dir = os.path.join(FRONTEND_SRC, "components", "outreach")
    forbidden = (
        "dangerouslySetInnerHTML",
        "innerHTML",
        "outerHTML",
        "insertAdjacentHTML",
        "document.write",
        "react-markdown",
        "marked",
        "markdown-it",
        "dompurify",
        "rehype",
        "remark",
        "html-react-parser",
    )
    scanned = 0
    for name in os.listdir(panel_dir):
        if name.endswith((".ts", ".tsx")):
            text = _code_only(open(os.path.join(panel_dir, name), encoding="utf-8").read())
            scanned += 1
            for token in forbidden:
                assert token not in text, f"{name} uses {token}"
    assert scanned >= 2
    panel = _code_only(open(os.path.join(panel_dir, "mailer-state-panel.tsx"), encoding="utf-8").read())
    assert (
        "{message.body}" in panel and "whitespace-pre-wrap" in panel
    )  # the body is a text node, newlines kept by CSS
    assert (
        "useOutreachMailerState" in panel and "useMutation" not in panel
    )  # read-only: no mutation in the panel


def test_the_panel_talks_only_to_leadboost_through_the_feature_api():
    hooks = open(os.path.join(FRONTEND_SRC, "features", "outreach", "api.ts"), encoding="utf-8").read()
    assert "/api/v2/outreach-actions/${actionId}/mailer-state" in hooks
    panel = _code_only(
        open(
            os.path.join(FRONTEND_SRC, "components", "outreach", "mailer-state-panel.tsx"), encoding="utf-8"
        ).read()
    )
    for token in ("apiClient", "axios", "http://", "https://"):
        assert token not in panel  # no direct HTTP at all from the component
    assert not re.search(r"\bfetch\(", panel)  # a real fetch() call (not the query's own refetch())
