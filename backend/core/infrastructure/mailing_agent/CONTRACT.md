# LeadBoost ⇄ Mailer contract (L1)

LeadBoost = **authorization + business context**. Mailer = **generation, communication,
delivery, conversation, and the send-time SMTP credential**. They are separate
deployments that talk only over HTTP; no code, model, ORM class or encryption key crosses.

> This replaces the P1.4 contract (`POST /outreach-actions`, `Authorization: Bearer`, an inline
> `sender{smtp_host,…,credential}` block and a pre-written `message{subject,body}`). That route
> never existed on the Mailer and is **removed**, not deprecated.

## Authentication and tenancy
* Header `X-API-Key: <key>`. **Mailer is the tenant authority**: it maps the key to its
  organization (`ORG_KEY_MAP`, `{"<key>":"<org_id>"}`). LeadBoost never sends an organization id
  in a body and nothing in a body is used for authorization.
* LeadBoost holds **one key per LeadBoost organization**:
  `MAILING_AGENT_ORG_API_KEYS={"<leadboost_org_id>":"<mailer_api_key>"}`. No key for the org ⇒
  fail closed (`mailing_agent_auth_not_configured`). A single shared key is not supported — it
  would collapse every customer into one Mailer organization. `MAILING_AGENT_API_KEY` is no longer read.
* `MAILING_AGENT_BASE_URL` must be `https://` (or `http://` to loopback only). Redirects are not followed.
* Operator provisions the per-org keys; automated tenant-key provisioning is deferred.

## 1. Outreach dispatch — **no credential**
`POST /integrations/leadboost/outreach-requests` → `202 {"accepted": true, "mailing_agent_reference": "<ref>"}`
```json
{
  "external_action_id": "<OutreachAction.id>",
  "idempotency_key":    "<OutreachAction.idempotency_key>",
  "correlation_id":     "<optional>",
  "recipient": {"email": "…", "name": "…", "title": "…", "company": "…"},
  "context":   {"value_proposition": "<Organization.description>", "recipient_facts": ["…"]}
}
```
Strict (unknown fields ⇒ 422 on the Mailer). Absent by design: sender, SMTP host/user/password,
credential, subject/body, tenant, mailbox reference. Mailer uses the organization's **sole ACTIVE
mailbox**; zero or several ⇒ `409`.

* `value_proposition` ← `Organization.description` (“What does your team do?”). Not set ⇒ the
  dispatch fails with `value_proposition_not_configured` (retryable); nothing is invented. Registration
  pre-fills this column with `Organization for <user e-mail>`; that placeholder is treated as **unset**
  (it is not an offer, and would put the user's address into the model prompt).
* `recipient_facts` ← real lead fields only (industry, about_text, founded year, employee band,
  revenue band). Never scores, qualification reasoning or other internal intelligence.
* Idempotency: the same action always sends the same `idempotency_key`; a retry after a lost
  response is replayed by the Mailer, never sent twice. `409` also covers key reuse for a different operation.
* Success needs a 2xx **and** a boolean `accepted`; anything else is a failure.

## 2. Mailbox provisioning — the **only** credential-bearing calls
| Call | Credential |
|---|---|
| `POST /mailboxes` (create, always ACTIVE) | yes, once |
| `GET /mailboxes` (adopt after a lost response / 409) | no |
| `PATCH /mailboxes/{ref}` `{"status":"disabled"}` | no |
| `PATCH /mailboxes/{ref}` `{"status":"active", smtp_host, smtp_port, smtp_use_tls, smtp_username, smtp_password}` | yes (atomic) |

* A mailbox is wanted **ACTIVE only when the LeadBoost account is active ∧ VERIFIED**. An
  unverified credential is never sent to the Mailer.
* Credential change / re-verification failure / disable ⇒ Mailer mailbox **disabled** first; the new
  config + credential go over only in the activation that follows a successful re-verification.
* `smtp_use_tls` is always `true` (STARTTLS). The Mailer has no implicit-TLS path, so accounts with
  `security_mode="tls"` are **not provisioned** (`unsupported_security_mode`).
* The Mailer stores its own encrypted copy; LeadBoost keeps its copy only for its own SMTP verification.
  Neither side's encryption key is ever transmitted. Credentials never appear in logs or responses.
* Identity is `(organization, email_address)`; `email_address` is sent only on create. Provisioning is
  idempotent: create → on `409` list and adopt by e-mail → activate.

## Known limitations (deliberate, documented — not hidden)
* **Snapshot ≠ delivered text.** `OutreachAction.subject/body` is LeadBoost's *legacy snapshot* (what was
  authorized/reviewed). The integrated path delivers the **Mailer's own generated `Message`**. The two are
  not synchronized and not claimed equivalent; `Lead.outreach_message` and the MessagingAgent are untouched.
  Human review of the Mailer's generated text is deferred.
* **No cross-service atomicity.** If LeadBoost disables an account but the Mailer disable call fails, the Mailer
  mailbox can stay ACTIVE while `mailer_sync_state='pending'` (retry: `POST …/mailer-sync`). LeadBoost dispatch
  requires `is_active ∧ VERIFIED ∧ mailer_sync_state='synced'`, so **no new dispatch** can start in that window;
  requests the Mailer had *already accepted* could still be sent until the retry converges.
* A mailbox created by the Mailer whose reference LeadBoost then lost, on an account that is *disabled before the
  next reconcile*, is not contacted (never provisioned ⇒ nothing to disable); it is adopted by e-mail the next time
  the account is activated, and until then a second ACTIVE mailbox makes the Mailer answer `409` (fail closed).
* Deferred: implicit TLS (`SMTP_SSL`), automated tenant-key provisioning, `display_name` in the Mailer, IMAP /
  inbound (M3), bilateral reconciliation (C9.3), removing LeadBoost's own encrypted credential.
