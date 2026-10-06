"""
In-memory stand-in for the separate Mailer service, for LeadBoost L1 tests.

It is mounted as an httpx.MockTransport underneath the REAL LeadBoost client
(core/infrastructure/mailing_agent), so every test exercises real request
construction, headers, JSON bodies and response parsing -- nothing is mocked
at the function level.

It reproduces the Mailer behaviours L1 depends on (verified against the
Mailer repository, and re-checked against the REAL Mailer in the cross-service
E2E):
  * the tenant comes ONLY from X-API-Key (unknown/missing key -> 401); a body can
    never name an organization
  * POST /mailboxes -> 201 ACTIVE, 409 when (organization, lower-cased email) exists
  * GET /mailboxes -> that organization's mailboxes only
  * PATCH /mailboxes/{ref} -> 404 when the ref is not the CALLER'S (a foreign ref is
    indistinguishable from an unknown one); status/host/port/use_tls/username/password
  * POST /integrations/leadboost/outreach-requests -> 202 {accepted, mailing_agent_reference},
    idempotent on idempotency_key (a replay returns the same reference, creates nothing)
  * secrets are stored but never returned

Fault injection: fail_next / timeout_next / connect_error_next / lose_response_next,
and `after_call` (a hook run after each handled call, to simulate LeadBoost state
changing while a Mailer call is in flight).
"""

import asyncio
import json
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

import httpx

REAL_ASYNC_CLIENT = httpx.AsyncClient  # captured before any test patches it

MAILBOX_FIELDS = {"email_address", "smtp_host", "smtp_port", "smtp_use_tls", "smtp_username", "smtp_password"}
PATCH_FIELDS = {"status", "smtp_host", "smtp_port", "smtp_use_tls", "smtp_username", "smtp_password"}


@dataclass
class Call:
    method: str
    path: str
    tenant: Optional[str]
    api_key: Optional[str]
    body: Any
    raw: bytes
    headers: Dict[str, str]
    status: int = 0


@dataclass
class FakeMailer:
    keys: Dict[str, str] = field(default_factory=dict)          # api_key -> mailer tenant
    mailboxes: Dict[str, dict] = field(default_factory=dict)    # ref -> record (incl. tenant, password)
    dispatches: Dict[tuple, dict] = field(default_factory=dict) # (tenant, idempotency_key) -> record
    calls: List[Call] = field(default_factory=list)
    after_call: Optional[Callable[[Call], None]] = None
    _faults: List[tuple] = field(default_factory=list)
    _lose_next_post: int = 0
    latency: float = 0.0

    # ---- fault injection -------------------------------------------------
    def fail_next(self, status: int = 503, n: int = 1):
        self._faults += [("status", status)] * n

    def timeout_next(self, n: int = 1):
        self._faults += [("timeout", None)] * n

    def connect_error_next(self, n: int = 1):
        self._faults += [("connect", None)] * n

    def lose_response_next_post(self, n: int = 1):
        """The create IS applied on the Mailer but the response never reaches LeadBoost."""
        self._lose_next_post += n

    # ---- introspection ---------------------------------------------------
    def tenant_boxes(self, tenant: str) -> List[dict]:
        return [m for m in self.mailboxes.values() if m["tenant"] == tenant]

    def active_boxes(self, tenant: str) -> List[dict]:
        return [m for m in self.tenant_boxes(tenant) if m["status"] == "active"]

    def calls_to(self, method: str, path_prefix: str) -> List[Call]:
        return [c for c in self.calls if c.method == method and c.path.startswith(path_prefix)]

    def credential_bearing_calls(self) -> List[Call]:
        return [c for c in self.calls if isinstance(c.body, dict) and "smtp_password" in c.body]

    # ---- the transport handler -------------------------------------------
    async def handler(self, request: httpx.Request) -> httpx.Response:
        if self.latency:
            await asyncio.sleep(self.latency)
        raw = request.content or b""
        try:
            body = json.loads(raw) if raw else None
        except ValueError:
            body = None
        api_key = request.headers.get("x-api-key")
        call = Call(request.method, request.url.path, self.keys.get(api_key), api_key, body, raw, dict(request.headers))
        self.calls.append(call)

        if self._faults:
            kind, value = self._faults.pop(0)
            if kind == "timeout":
                call.status = -1
                raise httpx.ReadTimeout("fake mailer timeout")
            if kind == "connect":
                call.status = -1
                raise httpx.ConnectError("fake mailer connect error")
            call.status = value
            return httpx.Response(value, json={"detail": "injected"})

        resp = self._route(call, request)
        call.status = resp.status_code
        if self._lose_next_post and call.method == "POST" and call.path == "/mailboxes" and resp.status_code == 201:
            self._lose_next_post -= 1
            call.status = -1
            raise httpx.ReadTimeout("response lost after the mailbox was created")
        if self.after_call:
            self.after_call(call)
        return resp

    def _route(self, call: Call, request: httpx.Request) -> httpx.Response:
        if call.tenant is None:
            return httpx.Response(401, json={"detail": "invalid api key"})
        t, b = call.tenant, call.body

        if call.path == "/mailboxes" and call.method == "POST":
            if not isinstance(b, dict) or set(b) - MAILBOX_FIELDS or not MAILBOX_FIELDS <= set(b):
                return httpx.Response(422, json={"detail": "invalid body"})
            email = b["email_address"].strip().lower()
            if any(m["tenant"] == t and m["email_address"] == email for m in self.mailboxes.values()):
                return httpx.Response(409, json={"detail": "mailbox exists"})
            ref = uuid.uuid4().hex
            self.mailboxes[ref] = dict(
                public_reference=ref, tenant=t, email_address=email, status="active",
                smtp_host=b["smtp_host"], smtp_port=b["smtp_port"], smtp_use_tls=b["smtp_use_tls"],
                smtp_username=b["smtp_username"], smtp_password=b["smtp_password"],
            )
            return httpx.Response(201, json=self._out(ref))

        if call.path == "/mailboxes" and call.method == "GET":
            return httpx.Response(200, json=[self._out(r) for r, m in self.mailboxes.items() if m["tenant"] == t])

        if call.path.startswith("/mailboxes/") and call.method == "PATCH":
            ref = call.path.split("/", 2)[2]
            m = self.mailboxes.get(ref)
            if m is None or m["tenant"] != t:
                return httpx.Response(404, json={"detail": "mailbox not found"})
            if not isinstance(b, dict) or set(b) - PATCH_FIELDS:
                return httpx.Response(422, json={"detail": "invalid body"})
            m.update(b)
            return httpx.Response(200, json=self._out(ref))

        if call.path == "/integrations/leadboost/outreach-requests" and call.method == "POST":
            if not isinstance(b, dict) or set(b) - {"external_action_id", "idempotency_key", "correlation_id", "recipient", "context"}:
                return httpx.Response(422, json={"detail": "invalid body"})
            key = (t, b["idempotency_key"])
            if key in self.dispatches:
                return httpx.Response(202, json={"accepted": True, "mailing_agent_reference": self.dispatches[key]["reference"]})
            if len(self.active_boxes(t)) != 1:
                return httpx.Response(409, json={"detail": "exactly one active mailbox required"})
            ref = f"dsp_{uuid.uuid4().hex[:10]}"
            self.dispatches[key] = dict(reference=ref, body=b, mailbox=self.active_boxes(t)[0]["public_reference"])
            return httpx.Response(202, json={"accepted": True, "mailing_agent_reference": ref})

        return httpx.Response(404, json={"detail": "no such route"})

    def _out(self, ref: str) -> dict:
        m = self.mailboxes[ref]
        return {k: m[k] for k in ("public_reference", "email_address", "status", "smtp_host", "smtp_port",
                                  "smtp_use_tls", "smtp_username")}

    # ---- wiring ----------------------------------------------------------
    def install(self, monkeypatch, mc) -> "FakeMailer":
        handler = self.handler

        def _factory(*args, **kwargs):
            kwargs["transport"] = httpx.MockTransport(handler)
            return REAL_ASYNC_CLIENT(*args, **kwargs)

        monkeypatch.setattr(mc.httpx, "AsyncClient", _factory)
        monkeypatch.setenv("MAILING_AGENT_BASE_URL", "https://mailer.test")
        return self

    def map_org(self, monkeypatch, leadboost_org_id: int) -> str:
        """Give a LeadBoost org its own Mailer API key -> its own Mailer tenant
        (the transitional MAILING_AGENT_ORG_API_KEYS mapping)."""
        api_key = f"key-org-{leadboost_org_id}"
        self.keys[api_key] = f"tenant-{leadboost_org_id}"
        current = {}
        import os
        if os.environ.get("MAILING_AGENT_ORG_API_KEYS"):
            current = json.loads(os.environ["MAILING_AGENT_ORG_API_KEYS"])
        current[str(leadboost_org_id)] = api_key
        monkeypatch.setenv("MAILING_AGENT_ORG_API_KEYS", json.dumps(current))
        return f"tenant-{leadboost_org_id}"
