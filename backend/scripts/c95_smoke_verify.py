#!/usr/bin/env python3
"""Read-only verifier for the hosted C9.5 smoke test (docs/C9_5_SMOKE_TEST.md). Standard library only.

It NEVER sends mail and never changes anything: it issues only GET requests (plus the one login POST that every
browser session makes) to endpoints that already exist, and asserts what those endpoints can prove.

Credentials come ONLY from the environment and are never printed or logged:
    LB_BASE_URL         LeadBoost API origin, https://... (plain http only for 127.0.0.1/localhost)
    LB_ACCESS_TOKEN     a LeadBoost bearer token ... or LB_EMAIL + LB_PASSWORD (one login POST, form-encoded)
    MAILER_BASE_URL     (optional) Mailer API origin;  MAILER_TENANT_KEY  the organisation's integration key.
                        With both set the verifier also reads the Mailer's own endpoints.

Usage:
    python scripts/c95_smoke_verify.py --sent-action ID [--cancelled-action ID] [--expect-inbound-replies N]
    python scripts/c95_smoke_verify.py --fixture bundle.json      # evaluate saved JSON (no network), used by the tests

Exit status: 0 every assertion held; 1 an assertion failed; 2 usage/configuration problem; 3 transport problem.

What this CAN prove through the API: the LeadBoost action and draft state sequence, that the approved digest equals the
digest of the subject/body the Mailer now holds for the outbound message, that exactly one outbound message of that
content reached a delivery state of `sent` (Mailer view: exactly one dispatch carries the action's reference), that the
cancelled draft has no sent twin, and the inbound-reply count after you answered from the second mailbox.
What it CANNOT prove (listed so nobody over-reads a green run): the bytes handed to SMTP, the RFC headers
(In-Reply-To / References / Message-ID), receipt in the second mailbox, DKIM/SPF results. Use the operator SQL and the
received message's raw headers described in the smoke-test document for those.
Message bodies and subjects are never printed (they are third-party text); digests are shown as 12-character prefixes.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field

EXIT_OK, EXIT_FAIL, EXIT_USAGE, EXIT_TRANSPORT = 0, 1, 2, 3
LOOPBACK = {"127.0.0.1", "localhost", "::1"}
SENT_LIKE = ("sent",)


class UsageError(Exception):
    pass


class TransportError(Exception):
    pass


def content_digest(subject: str | None, body: str) -> str:
    """Exactly mailer_agent/mail/content_digest.py (version 1): sha256 of the canonical JSON; None subject hashes as ''."""
    payload = json.dumps(
        {"body": body, "subject": subject or ""}, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass
class Check:
    name: str
    ok: bool
    detail: str = ""
    blocking: bool = True


@dataclass
class Report:
    checks: list[Check] = field(default_factory=list)

    def add(self, name: str, ok: bool, detail: str = "", blocking: bool = True) -> bool:
        self.checks.append(Check(name, bool(ok), detail, blocking))
        return bool(ok)

    @property
    def ok(self) -> bool:
        return all(c.ok for c in self.checks if c.blocking)


def short(d: str | None) -> str:
    return (d or "")[:12] or "-"


# ---------------------------------------------------------------------------------------------------------------
# Assertion logic: pure functions over JSON bundles (unit-tested on fixtures)
# ---------------------------------------------------------------------------------------------------------------
def outbound(messages: list[dict]) -> list[dict]:
    return [m for m in messages if m.get("direction") == "outbound"]


def inbound(messages: list[dict]) -> list[dict]:
    return [m for m in messages if m.get("direction") == "inbound"]


def evaluate_sent(r: Report, bundle: dict, expect_inbound_replies: int | None) -> None:
    """bundle = {action, draft, mailer_state, mailer_conversation?, mailer_draft?} for the reply that must have been SENT."""
    a, d, ms = bundle["action"], bundle["draft"], bundle["mailer_state"]
    r.add(
        "sent action: reply with a stored key and reply reference",
        bool(a.get("idempotency_key")) and bool(a.get("reply_to_message_reference")),
    )
    r.add(
        "sent action: LeadBoost state is submitted", a.get("state") == "submitted", f"state={a.get('state')}"
    )
    r.add(
        "sent action: an approver and approval time are recorded",
        a.get("approved_by_user_id") is not None and bool(a.get("approved_at")),
    )
    r.add(
        "sent action: approved draft version is 1 and an approved digest exists",
        a.get("approved_draft_version") == 1 and bool(a.get("approved_content_sha256")),
        f"digest={short(a.get('approved_content_sha256'))}",
    )
    # Informational: read from the code (approval -> claim -> one release), not yet observed on a deployed system.
    r.add(
        "sent action: one dispatch attempt (informational)",
        a.get("dispatch_attempts") == 1,
        f"attempts={a.get('dispatch_attempts')}",
        blocking=False,
    )
    r.add("draft read: state is sent", d.get("state") == "sent", f"state={d.get('state')}")
    r.add(
        "draft read: digest equals the digest approved on the action",
        bool(d.get("content_sha256")) and d.get("content_sha256") == a.get("approved_content_sha256"),
        f"draft={short(d.get('content_sha256'))} approved={short(a.get('approved_content_sha256'))}",
    )
    r.add(
        "draft read: the digest matches the subject/body the Mailer returns (recomputed here)",
        d.get("body") is not None and content_digest(d.get("subject"), d["body"]) == d.get("content_sha256"),
        f"recomputed={short(content_digest(d.get('subject'), d.get('body') or ''))}",
    )
    r.add(
        "mailer-state: available and delivery_state sent",
        ms.get("availability") == "available" and (ms.get("mailer") or {}).get("delivery_state") == "sent",
        f"availability={ms.get('availability')} delivery={(ms.get('mailer') or {}).get('delivery_state')}",
    )
    msgs = (ms.get("mailer") or {}).get("messages", [])
    window_complete = not (ms.get("mailer") or {}).get("has_more", False)
    twins = [
        m
        for m in outbound(msgs)
        if not m.get("body_truncated")
        and content_digest(m.get("subject"), m.get("body", "")) == a.get("approved_content_sha256")
    ]
    sent_twins = [m for m in twins if m.get("delivery_state") in SENT_LIKE]
    r.add(
        "conversation: exactly ONE outbound message has the approved digest and is `sent`",
        len(sent_twins) == 1,
        f"twins={len(twins)} sent={len(sent_twins)}"
        + ("" if window_complete else " (window truncated: has_more=true)"),
    )
    if expect_inbound_replies is not None:
        n = len(inbound(msgs))
        r.add(
            f"conversation: at least {expect_inbound_replies} inbound message(s) (the original and your reply from mailbox 2)",
            n >= expect_inbound_replies,
            f"inbound={n}",
        )
    mc = bundle.get("mailer_conversation")
    if mc is not None:
        ref = a.get("mailing_agent_reference")
        refs = [m for m in outbound(mc.get("messages", [])) if m.get("mailing_agent_reference") == ref]
        r.add(
            "MAILER view: exactly one outbound message carries this action's dispatch reference",
            len(refs) == 1,
            f"matches={len(refs)}",
        )
        r.add(
            "MAILER view: that message is `sent`", len(refs) == 1 and refs[0].get("delivery_state") == "sent"
        )
        r.add("MAILER view: the action state is sent", (mc.get("action") or {}).get("state") == "sent")
    md = bundle.get("mailer_draft")
    if md is not None:
        r.add(
            "MAILER draft endpoint: state sent and the same digest",
            md.get("state") == "sent" and md.get("content_sha256") == a.get("approved_content_sha256"),
        )


def evaluate_cancelled(r: Report, bundle: dict, sent_bundle: dict | None) -> None:
    a, d, ms = bundle["action"], bundle["draft"], bundle["mailer_state"]
    r.add(
        "cancelled action: LeadBoost state is cancelled",
        a.get("state") == "cancelled",
        f"state={a.get('state')}",
    )
    r.add(
        "cancelled action: no approval was ever recorded",
        not a.get("approved_content_sha256") and not a.get("submitted_at"),
    )
    r.add(
        "cancelled action: zero dispatch attempts (informational)",
        a.get("dispatch_attempts") == 0,
        f"attempts={a.get('dispatch_attempts')}",
        blocking=False,
    )
    r.add(
        "cancelled draft read: state is cancelled", d.get("state") == "cancelled", f"state={d.get('state')}"
    )
    digest = d.get("content_sha256")
    r.add(
        "cancelled draft: its content is still readable and its digest recomputes",
        bool(digest) and d.get("body") is not None and content_digest(d.get("subject"), d["body"]) == digest,
        f"digest={short(digest)}",
    )
    msgs = (ms.get("mailer") or {}).get("messages", [])
    sent_with_digest = [
        m
        for m in outbound(msgs)
        if m.get("delivery_state") == "sent"
        and not m.get("body_truncated")
        and content_digest(m.get("subject"), m.get("body", "")) == digest
    ]
    r.add(
        "conversation: NO `sent` outbound message carries the cancelled draft's digest",
        not sent_with_digest and bool(digest),
        f"sent twins={len(sent_with_digest)}",
    )
    if sent_bundle is not None:
        r.add(
            "the cancelled draft differs from the sent one (two different drafts were exercised)",
            digest != sent_bundle["action"].get("approved_content_sha256"),
        )
    md = bundle.get("mailer_draft")
    if md is not None:
        r.add(
            "MAILER draft endpoint: state cancelled",
            md.get("state") == "cancelled",
            f"state={md.get('state')}",
        )


# ---------------------------------------------------------------------------------------------------------------
# Transport (GET only; HTTPS unless loopback; credentials never echoed)
# ---------------------------------------------------------------------------------------------------------------
def check_origin(url: str, what: str) -> str:
    p = urllib.parse.urlsplit(url)
    if p.scheme == "https" and p.hostname:
        return url.rstrip("/")
    if p.scheme == "http" and (p.hostname or "") in LOOPBACK:
        return url.rstrip("/")
    raise UsageError(f"{what} must be https:// (http only for 127.0.0.1/localhost)")


def http_json(
    url: str, headers: dict, data: bytes | None = None, timeout: int = 30
) -> tuple[int, dict | None]:
    req = urllib.request.Request(
        url, data=data, headers=headers, method="POST" if data is not None else "GET"
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 (scheme checked above)
            raw = resp.read()
            return resp.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as e:
        try:
            body = json.loads(e.read() or b"null")
        except ValueError:
            body = None
        return e.code, body
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise TransportError(f"{type(e).__name__} for {urllib.parse.urlsplit(url).netloc}") from None


class Client:
    def __init__(self, env: dict):
        base = env.get("LB_BASE_URL", "")
        if not base:
            raise UsageError("LB_BASE_URL is not set")
        self.lb = check_origin(base, "LB_BASE_URL")
        self.token = env.get("LB_ACCESS_TOKEN") or ""
        if not self.token:
            email, pw = env.get("LB_EMAIL"), env.get("LB_PASSWORD")
            if not (email and pw):
                raise UsageError("set LB_ACCESS_TOKEN, or LB_EMAIL and LB_PASSWORD")
            form = urllib.parse.urlencode({"username": email, "password": pw}).encode()
            code, body = http_json(
                f"{self.lb}/api/v2/login", {"Content-Type": "application/x-www-form-urlencoded"}, form
            )
            if code != 200 or not body or not body.get("access_token"):
                raise UsageError(f"LeadBoost login failed (HTTP {code})")
            self.token = body["access_token"]
        self.mailer = (
            check_origin(env["MAILER_BASE_URL"], "MAILER_BASE_URL") if env.get("MAILER_BASE_URL") else None
        )
        self.mailer_key = env.get("MAILER_TENANT_KEY") or ""
        if self.mailer and not self.mailer_key:
            raise UsageError("MAILER_BASE_URL needs MAILER_TENANT_KEY")

    def lb_get(self, path: str):
        return http_json(
            f"{self.lb}{path}", {"Authorization": f"Bearer {self.token}", "Accept": "application/json"}
        )

    def mailer_get(self, path: str):
        return http_json(f"{self.mailer}{path}", {"X-API-Key": self.mailer_key, "Accept": "application/json"})

    def fetch_bundle(self, action_id: int) -> dict:
        out: dict = {}
        for key, path in (
            ("action", f"/api/v2/outreach-actions/{action_id}"),
            ("draft", f"/api/v2/outreach-actions/{action_id}/reply-draft"),
            ("mailer_state", f"/api/v2/outreach-actions/{action_id}/mailer-state"),
        ):
            code, body = self.lb_get(path)
            if code != 200 or not isinstance(body, dict):
                raise TransportError(f"LeadBoost {key} read answered HTTP {code}")
            out[key] = body
        if self.mailer:
            key = urllib.parse.quote(out["action"]["idempotency_key"], safe="")
            code, body = self.mailer_get(
                f"/integrations/leadboost/outreach-actions/{key}/conversation?limit=50"
            )
            out["mailer_conversation"] = body if code == 200 else None
            code, body = self.mailer_get(f"/integrations/leadboost/reply-drafts/{key}")
            out["mailer_draft"] = body if code == 200 else None
        return out


def render(report: Report) -> str:
    lines = [
        f"  {'PASS' if c.ok else ('FAIL' if c.blocking else 'NOTE')}  {c.name}"
        + (f"   ({c.detail})" if c.detail else "")
        for c in report.checks
    ]
    lines.append(
        f"  RESULT: {'ALL ASSERTIONS HELD' if report.ok else 'AT LEAST ONE ASSERTION FAILED'}  ({sum(c.ok for c in report.checks if c.blocking)}/{sum(1 for c in report.checks if c.blocking)} blocking)"
    )
    return "\n".join(lines)


def main(argv=None, env=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--sent-action", type=int)
    ap.add_argument("--cancelled-action", type=int)
    ap.add_argument(
        "--expect-inbound-replies",
        type=int,
        default=None,
        help="minimum inbound messages in the conversation (e.g. 2)",
    )
    ap.add_argument(
        "--fixture", help="evaluate saved JSON {sent:{...}, cancelled:{...}} instead of calling the network"
    )
    a = ap.parse_args(argv)
    env = os.environ if env is None else env
    report = Report()
    try:
        if a.fixture:
            data = json.load(open(a.fixture, encoding="utf-8"))
            sent, cancelled = data.get("sent"), data.get("cancelled")
        else:
            if a.sent_action is None:
                raise UsageError("--sent-action is required")
            c = Client(env)
            sent = c.fetch_bundle(a.sent_action)
            cancelled = c.fetch_bundle(a.cancelled_action) if a.cancelled_action is not None else None
        if sent:
            evaluate_sent(report, sent, a.expect_inbound_replies)
        if cancelled:
            evaluate_cancelled(report, cancelled, sent)
    except UsageError as e:
        print(f"USAGE: {e}", file=sys.stderr)
        return EXIT_USAGE
    except TransportError as e:
        print(f"TRANSPORT: {e}", file=sys.stderr)
        return EXIT_TRANSPORT
    print(render(report))
    print(
        "  NOT PROVABLE VIA THE API: the bytes handed to SMTP; In-Reply-To/References/Message-ID; receipt in mailbox 2; SPF/DKIM/DMARC results."
    )
    return EXIT_OK if report.ok else EXIT_FAIL


if __name__ == "__main__":
    sys.exit(main())
