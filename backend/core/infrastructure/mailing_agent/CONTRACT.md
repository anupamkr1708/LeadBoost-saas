# Mailing Agent contract (P1.4)

This is the complete, narrow contract between LeadBoost and the separate
Mailing Agent service. It exists so the Mailing Agent (a different
repository/deployment) can be implemented against a fixed spec without
needing LeadBoost's database, ORM models, or encryption key. The
reference implementation of the LeadBoost side is
`core/infrastructure/mailing_agent/client.py` — if this document and
that file ever disagree, the code is what actually ships; please update
this document to match rather than the other way around.

## Request

`POST {MAILING_AGENT_BASE_URL}/outreach-actions`

Headers:
- `Content-Type: application/json`
- `Authorization: Bearer {MAILING_AGENT_API_KEY}` (omitted if no API key is configured)

Body:

```json
{
  "outreach_action_id": 123,
  "organization_id": 45,
  "idempotency_key": "auto:9f2c...",
  "correlation_id": "pipeline-run-id-or-null",
  "sender": {
    "email_address": "sales@example.com",
    "display_name": "Acme Sales",
    "smtp_host": "smtp.example.com",
    "smtp_port": 587,
    "security_mode": "starttls",
    "username": "sales@example.com",
    "credential_type": "smtp_password",
    "credential": "the-plaintext-secret"
  },
  "recipient": {
    "email": "lead@example.com",
    "name": "Jamie Lead"
  },
  "message": {
    "subject": "Quick note for Acme Co",
    "body": "Hi Jamie, ..."
  }
}
```

Every field above is sent on every request; none are conditionally
omitted except `correlation_id`, `sender.display_name`, and
`recipient.name`, which may be `null`.

## Response

Any 2xx with a JSON body:

```json
{ "accepted": true, "mailing_agent_reference": "some-id-you-assign" }
```

`accepted` is **required** and must be a JSON boolean — LeadBoost treats a
missing, non-boolean, or otherwise malformed `accepted` field as an
invalid response (never as an implicit success), and moves the
OutreachAction to `dispatch_failed` rather than guessing. `mailing_agent_reference`
is optional; LeadBoost stores it verbatim on the OutreachAction purely
for cross-system correlation in logs/support — it is never interpreted.
`accepted: false` in an otherwise-2xx response is treated by LeadBoost
the same as a 4xx rejection.

Any non-2xx response (1xx/3xx included — LeadBoost's client does not
follow redirects), a connection failure, or a timeout is treated as a
failed dispatch attempt — LeadBoost moves the OutreachAction to
`dispatch_failed` and the caller may retry the same LeadBoost endpoint
later, which will send another request with the **same**
`idempotency_key`.

## Idempotency — hard requirement, not a hint

**For a given `idempotency_key`, every request LeadBoost sends is the
same logical delivery operation, and the Mailing Agent MUST treat repeat
requests accordingly: at most one email is ever actually sent for that
key.** If the Mailing Agent receives a request whose `idempotency_key`
it has already seen, it must not send a second email — it should return
the same logical result (or an equivalent already-processed
acknowledgment) instead.

This matters because LeadBoost cannot fully resolve the standard
distributed-systems ambiguity on its own:

```
LeadBoost -> Mailing Agent -> mail actually sent -> response lost to a
network timeout -> LeadBoost doesn't know whether the send happened ->
LeadBoost (or its user) retries with the same idempotency_key
```

LeadBoost's own dispatch path already guarantees it will never make two
*concurrent* requests for the same OutreachAction (an atomic
`DISPATCHING`-state claim — see `outreach_action.py::OutreachState` and
`outreach_service.py::dispatch_action`) — but that only protects against
LeadBoost racing itself. It does not, and cannot, protect against a
legitimate *sequential* retry after an ambiguous failure. The
idempotency key is how that second half of the guarantee is enforced,
and it must be enforced on the Mailing Agent's side.

## Transport security

`MAILING_AGENT_BASE_URL` must be `https://`. The one exception is local
development, where `http://localhost`, `http://127.0.0.1`, and
`http://[::1]` (with or without an explicit port) are accepted, since
there is no network segment for `sender.credential` to leak across.
LeadBoost refuses to send this request at all — before making any
network call — if the configured URL is `http://` and does not point at
one of those local hosts.

## Authentication

For any `MAILING_AGENT_BASE_URL` that is **not** one of the local-loopback
hosts above, `MAILING_AGENT_API_KEY` is **required**. LeadBoost sends it
as `Authorization: Bearer {MAILING_AGENT_API_KEY}` and refuses to send
the request at all — again, before any network call — if a remote
destination is configured with no API key set. Local development keeps
the API key optional. HTTPS alone protects the transport; requiring
authentication too means a credential-bearing call to a real, remote
Mailing Agent is never anonymous.

## What is intentionally never sent

- No SQLAlchemy models, ORM relationships, or raw DB rows.
- `EMAIL_CREDENTIAL_ENCRYPTION_KEY`, or any LeadBoost encryption key
  material.
- Lead intelligence unrelated to sending this message (AI score,
  qualification reasoning, company-intelligence output, etc.).
- Any LeadBoost internal id the Mailing Agent has no use for.
  `organization_id` is the one exception, included purely so the
  Mailing Agent's own logs/audit trail can group requests by tenant.

## What the Mailing Agent must never do with this request

- Persist `sender.credential` beyond what is strictly necessary to
  complete this one send.
- Log `sender.credential`, or the request body verbatim if it contains
  the credential.
- Return `sender.credential` (or any credential) in its response.
- Send a second email for an `idempotency_key` it has already processed.
