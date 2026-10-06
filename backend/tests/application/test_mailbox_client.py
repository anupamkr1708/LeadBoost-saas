"""
L1: Mailer mailbox provisioning client (core/infrastructure/mailing_agent/mailbox_client.py).

The SMTP credential may be placed in a request body by exactly two operations:
create and activate. These tests inspect the real wire request for all four.
"""

import json

import httpx
import pytest

from core.infrastructure.mailing_agent import client as mc
from core.infrastructure.mailing_agent import mailbox_client as mb

ORG, KEY, SECRET = 3, "org-3-key", "S3cret-PW-do-not-leak"
_REAL_ASYNC_CLIENT = httpx.AsyncClient  # captured before any test patches it


@pytest.fixture()
def wire(monkeypatch):
    seen = []
    real = _REAL_ASYNC_CLIENT

    def _factory(*a, **k):
        def handler(req):
            seen.append(req)
            return httpx.Response(200, json={"public_reference": "ref/1"})

        k["transport"] = httpx.MockTransport(handler)
        return real(*a, **k)

    monkeypatch.setattr(mc.httpx, "AsyncClient", _factory)
    monkeypatch.setenv("MAILING_AGENT_BASE_URL", "https://mailer.example.com")
    monkeypatch.setenv("MAILING_AGENT_ORG_API_KEYS", json.dumps({str(ORG): KEY}))
    return seen


CONN = dict(smtp_host="smtp.example.com", smtp_port=587, smtp_username="u@example.com", smtp_password=SECRET)


async def test_create_is_the_credential_bearing_post(wire):
    await mb.create_mailbox(ORG, email_address="u@example.com", **CONN)
    req = wire[0]
    assert (req.method, req.url.path) == ("POST", "/mailboxes")
    assert req.headers["x-api-key"] == KEY and "authorization" not in req.headers
    assert json.loads(req.content) == {
        "email_address": "u@example.com", "smtp_host": "smtp.example.com", "smtp_port": 587,
        "smtp_use_tls": True, "smtp_username": "u@example.com", "smtp_password": SECRET,
    }


async def test_activate_is_one_atomic_patch_with_status_transport_and_credential(wire):
    await mb.activate_mailbox(ORG, "ref1", **CONN)
    req = wire[0]
    assert (req.method, req.url.path) == ("PATCH", "/mailboxes/ref1")
    assert json.loads(req.content) == {
        "status": "active", "smtp_host": "smtp.example.com", "smtp_port": 587,
        "smtp_use_tls": True, "smtp_username": "u@example.com", "smtp_password": SECRET,
    }
    assert "email_address" not in json.loads(req.content)   # identity is never part of an update


async def test_disable_and_list_carry_no_credential_at_all(wire):
    await mb.disable_mailbox(ORG, "ref1")
    await mb.list_mailboxes(ORG)
    assert (wire[0].method, json.loads(wire[0].content)) == ("PATCH", {"status": "disabled"})
    assert (wire[1].method, wire[1].url.path, wire[1].content) == ("GET", "/mailboxes", b"")
    assert all(SECRET.encode() not in r.content and SECRET not in str(r.headers) for r in wire)


async def test_only_create_and_activate_can_carry_a_password(wire):
    await mb.create_mailbox(ORG, email_address="u@example.com", **CONN)
    await mb.activate_mailbox(ORG, "r", **CONN)
    await mb.disable_mailbox(ORG, "r")
    await mb.list_mailboxes(ORG)
    carrying = [(r.method, r.url.path) for r in wire if b"smtp_password" in r.content]
    assert carrying == [("POST", "/mailboxes"), ("PATCH", "/mailboxes/r")]


async def test_reference_cannot_alter_the_request_path(wire):
    await mb.disable_mailbox(ORG, "../mailboxes/x?y=1#z")
    assert wire[0].url.raw_path.decode().startswith("/mailboxes/..%2Fmailboxes%2Fx%3Fy%3D1%23z")


async def test_mailbox_calls_use_the_same_fail_closed_transport_rules(wire, monkeypatch):
    monkeypatch.setenv("MAILING_AGENT_BASE_URL", "http://mailer.example.com")  # remote http: would carry a password
    res = await mb.create_mailbox(ORG, email_address="u@example.com", **CONN)
    assert res.error_code == mc.DispatchErrorCode.INSECURE_TRANSPORT and wire == []
    monkeypatch.setenv("MAILING_AGENT_BASE_URL", "https://mailer.example.com")
    res = await mb.create_mailbox(99, email_address="u@example.com", **CONN)   # org with no key
    assert res.error_code == mc.DispatchErrorCode.AUTH_NOT_CONFIGURED and wire == []


async def test_a_redirect_is_never_followed_with_the_credential(wire, monkeypatch):
    def _redirect(request):
        wire.append(request)
        return httpx.Response(307, headers={"location": "https://evil.example/"})

    def _factory(*a, **k):
        k["transport"] = httpx.MockTransport(_redirect)
        return _REAL_ASYNC_CLIENT(*a, **k)

    monkeypatch.setattr(mc.httpx, "AsyncClient", _factory)
    res = await mb.create_mailbox(ORG, email_address="u@example.com", **CONN)
    assert res.status_code == 307 and len(wire) == 1
