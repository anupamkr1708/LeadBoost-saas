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
  * GET /integrations/leadboost/outreach-actions/{key}/conversation?limit=N (C9.3) -> 200
    {action, messages, has_more} for the CALLER'S dispatch only (404 for unknown or
    foreign keys, indistinguishable); the key may contain '/', so the whole segment
    between the fixed prefix and "/conversation" is the key
  * (C9.5) a request carrying `in_reply_to` is a MANAGED REPLY: it is held, never sent, until released. Script the
    Mailer's side with generate_draft / fail_generation / mutate_draft. GET/POST /integrations/leadboost/reply-drafts/
    {key}[/release|/cancel] follow the real Mailer's contract (statuses and `detail` codes included): a foreign or
    unknown key is 404; release re-computes the digest from the CURRENT stored content and is idempotent for the
    same digest; cancel is refused once released. The digest below is a stand-in with the real algorithm's
    definition (canonical JSON of {"body","subject"} -> SHA-256); LeadBoost treats it as opaque, and the
    cross-service E2E checks it against the REAL Mailer.
  * secrets are stored but never returned

Fault injection: fail_next / timeout_next / connect_error_next / lose_response_next,
and `after_call` (a hook run after each handled call, to simulate LeadBoost state
changing while a Mailer call is in flight).
"""

import asyncio
import hashlib
import json
import re
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

import httpx

REAL_ASYNC_CLIENT = httpx.AsyncClient  # captured before any test patches it

MAILBOX_FIELDS = {"email_address", "smtp_host", "smtp_port", "smtp_use_tls", "smtp_username", "smtp_password"}
CONVERSATION_PREFIX = "/integrations/leadboost/outreach-actions/"
DRAFT_PREFIX = "/integrations/leadboost/reply-drafts/"
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
    raw_target: str = ""  # path + query exactly as sent (percent-encoding intact)


# The real Mailer's reply contract: an inbound message's reference is an opaque 32-lowercase-hex token.
REPLY_TOKEN_RE = re.compile(r"^[0-9a-f]{32}$")


def fake_content_digest(subject: Optional[str], body: str) -> str:
    """Same definition as the real Mailer's content digest (mailer_agent/mail/content_digest.py, version 1)."""
    payload = json.dumps({"body": body, "subject": subject or ""}, sort_keys=True, separators=(",", ":"),
                         ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def fake_reply_token(tenant: str, idempotency_key: str, ordinal: int) -> str:
    """Deterministic, valid 32-hex token for the ``ordinal``-th message of a scripted conversation."""
    return hashlib.md5(f"fake-mailer|{tenant}|{idempotency_key}|{ordinal}".encode(), usedforsecurity=False).hexdigest()


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
        call = Call(request.method, request.url.path, self.keys.get(api_key), api_key, body, raw, dict(request.headers),
                    raw_target=request.url.raw_path.decode("ascii"))
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
            if not isinstance(b, dict) or set(b) - {"external_action_id", "idempotency_key", "correlation_id", "recipient", "context", "in_reply_to"}:
                return httpx.Response(422, json={"detail": "invalid body"})
            rt = b.get("in_reply_to")
            if "in_reply_to" in b and not (
                isinstance(rt, dict) and set(rt) == {"message_reference"}
                and isinstance(rt["message_reference"], str) and REPLY_TOKEN_RE.fullmatch(rt["message_reference"])
            ):
                return httpx.Response(422, json={"detail": "invalid in_reply_to"})
            key = (t, b["idempotency_key"])
            if key in self.dispatches:
                return httpx.Response(202, json={"accepted": True, "mailing_agent_reference": self.dispatches[key]["reference"]})
            if len(self.active_boxes(t)) != 1:
                return httpx.Response(409, json={"detail": "exactly one active mailbox required"})
            ref = f"dsp_{uuid.uuid4().hex[:10]}"
            self.dispatches[key] = dict(
                reference=ref, body=b, mailbox=self.active_boxes(t)[0]["public_reference"],
                state="queued", messages=[], created_at="2026-01-01T12:00:00Z", updated_at="2026-01-01T12:00:00Z",
                reply="in_reply_to" in b, draft=None, approved=None,
            )
            return httpx.Response(202, json={"accepted": True, "mailing_agent_reference": ref})

        if call.method == "GET" and call.path.startswith(CONVERSATION_PREFIX) and call.path.endswith("/conversation"):
            key = call.path[len(CONVERSATION_PREFIX):-len("/conversation")]
            try:
                limit = int(request.url.params.get("limit", "20"))
            except ValueError:
                limit = -1
            if not 1 <= limit <= 50:
                return httpx.Response(422, json={"detail": "invalid limit"})
            rec = self.dispatches.get((t, key))
            if rec is None:
                return httpx.Response(404, json={"detail": "Outreach action not found"})
            return httpx.Response(200, json=self._conversation_out(rec, limit))

        if call.path.startswith(DRAFT_PREFIX):
            return self._route_draft(call, t, b)

        return httpx.Response(404, json={"detail": "no such route"})

    # ---- C9.5 managed-reply drafts ---------------------------------------
    def _draft_view(self, rec: dict) -> dict:
        d, st = rec["draft"], rec["state"]
        state = "generating" if st == "queued" and d is None else st
        out = dict(state=state, subject=None, body=None, content_sha256=None, draft_version=None,
                   updated_at=rec["updated_at"])
        if d is not None:
            out.update(subject=d["subject"], body=d["body"],
                       content_sha256=fake_content_digest(d["subject"], d["body"]), draft_version=1)
        return out

    def _route_draft(self, call: Call, t: str, b) -> httpx.Response:
        rest = call.path[len(DRAFT_PREFIX):]
        action = None
        for suffix in ("/release", "/cancel"):
            if rest.endswith(suffix):
                rest, action = rest[: -len(suffix)], suffix[1:]
        rec = self.dispatches.get((t, rest))
        if rec is None or not rec.get("reply"):
            return httpx.Response(404, json={"detail": "Reply draft not found"})   # foreign == unknown == ordinary
        if rec.get("legacy"):
            return self._route_legacy_draft(call, action, rec)

        if action is None and call.method == "GET":
            return httpx.Response(200, json=self._draft_view(rec))

        if action == "release" and call.method == "POST":
            digest = b.get("content_sha256") if isinstance(b, dict) and set(b) == {"content_sha256"} else None
            if not (isinstance(digest, str) and re.fullmatch(r"[0-9a-f]{64}", digest)):
                return httpx.Response(422, json={"detail": "invalid content_sha256"})
            if rec["state"] == "awaiting_approval":
                d = rec["draft"]
                if fake_content_digest(d["subject"], d["body"]) != digest:
                    return httpx.Response(409, json={"detail": "draft_changed"})
                rec["state"], rec["approved"] = "queued", digest
                return httpx.Response(200, json=self._draft_view(rec))
            if rec["approved"] is not None:
                if rec["approved"] == digest:
                    return httpx.Response(200, json=self._draft_view(rec))   # idempotent replay
                return httpx.Response(409, json={"detail": "draft_already_released_with_different_content"})
            detail = {"cancelled": "draft_cancelled", "failed": "draft_generation_failed"}.get(
                rec["state"], "draft_not_ready")
            return httpx.Response(409, json={"detail": detail})

        if action == "cancel" and call.method == "POST":
            if rec["state"] == "cancelled":
                return httpx.Response(200, json=self._draft_view(rec))
            if rec["state"] in ("awaiting_approval", "generating") or (rec["state"] == "queued" and rec["draft"] is None):
                rec["state"] = "cancelled"
                return httpx.Response(200, json=self._draft_view(rec))
            return httpx.Response(409, json={"detail": "draft_already_released"})

        return httpx.Response(404, json={"detail": "no such route"})

    def _route_legacy_draft(self, call: Call, action: Optional[str], rec: dict) -> httpx.Response:
        """A reply accepted by the PRE-approval Mailer (C9.4): the row exists, with the new flag false. The real
        Mailer never answers 404 for it and never writes to it: read / release / cancel are all a 409
        `legacy_reply_requires_reconciliation`, except that cancel is a 200 (no content) where the Mailer can PROVE
        nothing was or can be sent (`failed`, or `cancelled` by an operator)."""
        if action == "cancel" and call.method == "POST" and rec["state"] in ("failed", "cancelled"):
            return httpx.Response(200, json=dict(state=rec["state"], subject=None, body=None, content_sha256=None,
                                                 draft_version=None, updated_at=rec["updated_at"]))
        if (action, call.method) in ((None, "GET"), ("release", "POST"), ("cancel", "POST")):
            return httpx.Response(409, json={"detail": "legacy_reply_requires_reconciliation"})
        return httpx.Response(404, json={"detail": "no such route"})

    def make_legacy_dispatch(self, tenant: str, idempotency_key: str, state: str, *, message: bool = True) -> dict:
        """Seed a C9.4-era Mailer operation under this key (as it exists after the Mailer's migration 013)."""
        rec = dict(reference=f"dsp_legacy_{uuid.uuid4().hex[:6]}", body={}, mailbox=None, state=state, messages=[],
                   created_at="2026-01-01T12:00:00Z", updated_at="2026-01-01T12:00:00Z", reply=True, legacy=True,
                   has_message=message, draft=None, approved=None)
        self.dispatches[(tenant, idempotency_key)] = rec
        return rec

    def _out(self, ref: str) -> dict:
        m = self.mailboxes[ref]
        return {k: m[k] for k in ("public_reference", "email_address", "status", "smtp_host", "smtp_port",
                                  "smtp_use_tls", "smtp_username")}

    def _conversation_out(self, rec: dict, limit: int) -> dict:
        msgs = rec["messages"]
        window = msgs[-limit:]
        out = []
        public_state = {"awaiting_approval": "queued", "cancelled": "failed"}.get(rec["state"], rec["state"])
        for m in window:
            outbound = m["direction"] == "outbound"
            out.append(dict(
                direction=m["direction"], message_type="initial_outreach" if outbound else None,
                subject=m["subject"], body=m["body"], body_truncated=False, created_at=m["created_at"],
                delivery_state=public_state if outbound else None,
                mailing_agent_reference=rec["reference"] if outbound else None,
                mailbox_reference=rec["mailbox"],
                # like the real Mailer: always present; None on outbound, the stable opaque 32-hex token on inbound
                message_reference=None if outbound else m["message_reference"],
            ))
        return {
            "action": {"accepted": True, "state": public_state, "mailing_agent_reference": rec["reference"],
                       "created_at": rec["created_at"], "updated_at": rec["updated_at"], "mailbox_reference": rec["mailbox"]},
            "messages": out,
            "has_more": len(msgs) > limit,
        }

    # ---- conversation scripting (C9.3) -----------------------------------
    def dispatch_record(self, tenant: str, idempotency_key: str) -> dict:
        return self.dispatches[(tenant, idempotency_key)]

    def set_dispatch_state(self, tenant: str, idempotency_key: str, state: str):
        self.dispatch_record(tenant, idempotency_key)["state"] = state

    # ---- managed-reply scripting (C9.5): what the Mailer's worker would do ----
    def generate_draft(self, tenant: str, idempotency_key: str, *, subject: str = "Re: Hello",
                       body: str = "Hi Jane,\n\nThanks for getting back to me. Happy to share more.\n\nBest,\nSender"):
        """The generation worker finished: the draft is persisted and HELD (awaiting_approval, not sendable)."""
        rec = self.dispatch_record(tenant, idempotency_key)
        assert rec.get("reply"), "only a managed reply is held"
        rec["draft"] = dict(subject=subject, body=body)
        rec["state"] = "awaiting_approval"
        return fake_content_digest(subject, body)

    def fail_generation(self, tenant: str, idempotency_key: str):
        rec = self.dispatch_record(tenant, idempotency_key)
        rec["draft"], rec["state"] = None, "failed"

    def mutate_draft(self, tenant: str, idempotency_key: str, *, body: Optional[str] = None,
                     subject: Optional[str] = None):
        d = self.dispatch_record(tenant, idempotency_key)["draft"]
        if body is not None:
            d["body"] = body
        if subject is not None:
            d["subject"] = subject

    def add_message(self, tenant: str, idempotency_key: str, *, direction: str, body: str,
                    subject: Optional[str] = "Quick question", created_at: str = "2026-01-01T12:00:00Z",
                    message_reference: Optional[str] = None):
        rec = self.dispatch_record(tenant, idempotency_key)
        m = dict(direction=direction, body=body, subject=subject, created_at=created_at)
        if direction == "inbound":
            # Assigned once, here, so the token is stable however the conversation window slides.
            token = message_reference or fake_reply_token(tenant, idempotency_key, len(rec["messages"]))
            if not REPLY_TOKEN_RE.fullmatch(token):
                raise ValueError(f"message_reference must be 32 lowercase hex characters, got {token!r}")
            m["message_reference"] = token
        elif message_reference is not None:
            raise ValueError("outbound messages carry no message_reference")
        rec["messages"].append(m)

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
