# LeadBoost ⇄ Mailer Agent Integration

How LeadBoost and the separately deployed Mailer Agent cooperate to take a qualified lead to a sent email and
a visible conversation. This page explains the contract from the **product's** end-to-end perspective. It does
not duplicate the wire contract; the canonical references are:

* [`../core/infrastructure/mailing_agent/CONTRACT.md`](../core/infrastructure/mailing_agent/CONTRACT.md): LeadBoost-side contract (L1 + C9.3)
* [`LEADBOOST_L1_MAILBOX_CONTRACT.md`](https://github.com/anupamkr1708/LeadBoost-mail-agent/blob/main/docs/LEADBOOST_L1_MAILBOX_CONTRACT.md): Mailer-side mailbox contract
* [`C13_DEPLOYMENT_RUNBOOK.md`](https://github.com/anupamkr1708/LeadBoost-mail-agent/blob/main/docs/C13_DEPLOYMENT_RUNBOOK.md): Mailer deployment runbook

> **Deployment and verification status.** LeadBoost and the Mailer are deployed as separate services. The operator
> reports the integrated system running end to end on a local setup, and the hosted LeadBoost API and Mailer API
> and the hosted Mailer worker running as separate services. The synthetic cross-service lifecycle test is
> CI-verified. A hosted delivery acceptance record (dispatch receipt plus a message received in a recipient
> mailbox) is not included in this repository, so delivery to a real inbox is not asserted. See
> [`SYSTEM_DESIGN.md`](SYSTEM_DESIGN.md) §15 for the verification levels. This page describes the current
> implementation.

![Lifecycle swimlane: account verification, mailbox sync, authorization and dispatch, durable acceptance, generation, SMTP, IMAP reply intake and the read-only conversation view, with failure and unknown paths](diagrams/leadboost-mailer-lifecycle.svg)

*Diagram: [`leadboost-mailer-lifecycle.svg`](diagrams/leadboost-mailer-lifecycle.svg). Accepted ≠ sent ≠ delivered.*

## 1. Ownership

| Concern | Owner | Consequence |
|---|---|---|
| Customer identity, tenancy, leads, business context | LeadBoost | The Mailer never sees LeadBoost's database |
| The decision that an outreach may go out | LeadBoost (`OutreachAction`) | Approval, cancellation and quota live in LeadBoost |
| The text of the message actually sent | **Mailer** | LeadBoost's `subject`/`body` is an authorization *snapshot*, not the delivered text |
| Send-time SMTP credential, mailbox execution | Mailer | Mailer keeps its own encrypted copy |
| Inbound mail, conversation record | Mailer | |
| Authoritative delivery state | Mailer | LeadBoost displays it, never rewrites it |

The two services run separately and do **not** share ORM models, a database, a transaction, or an encryption
key. No distributed transaction spans them (see §6).

## 2. Authentication and transport

| Item | Behaviour |
|---|---|
| Header | `X-API-Key: <tenant key>` on every call |
| Tenant authority | The **Mailer**. It maps the presented key to its organization through `ORG_KEY_MAP` (`{"<key>": "<org_id>"}`). LeadBoost never sends an organization id; nothing in a body authorizes anything |
| LeadBoost side | `MAILING_AGENT_BASE_URL`, `MAILING_AGENT_ORG_API_KEYS` (`{"<leadboost_org_id>": "<mailer_key>"}`), `MAILING_AGENT_TIMEOUT_SECONDS` (default 15, must be finite and positive) |
| Unique key per organization | Each LeadBoost organization must map to a distinct Mailer API key. The client **rejects** a map in which two different entries carry the same non-empty key (compared after trimming whitespace) or in which an organization entry is repeated: the whole map is unusable, every call fails closed (`mailing_agent_auth_not_configured`) before any request is made, and only a fixed, value-free reason is logged (never a key, an organization id or the JSON). Missing key ⇒ fail closed (`mailing_agent_auth_not_configured`). |
| HTTPS | `MAILING_AGENT_BASE_URL` must be `https://`, or `http://` to a loopback host only (`localhost`, `127.0.0.1`, `::1`). Anything else ⇒ `mailing_agent_insecure_transport`; credential-bearing calls are refused |
| Redirects | Never followed, so a 3xx cannot forward a key or credential to another host |
| Operator credential | The Mailer has a **separate** `SYSTEM_OPERATOR_API_KEY` (header `X-System-Operator-Key`) for deployment-wide operator routes. LeadBoost never holds or sends it, and a tenant key does not grant operator access |
| Browser | Never receives the Mailer URL, a key, the key map or a Mailer path, and it calls LeadBoost's own API only. One known review item: the `OutreachAction` response still carries `mailing_agent_reference`, an opaque string (the conversation view drops it) |

Keys are provisioned by an operator; automated tenant-key provisioning is not implemented. Use placeholders
in any shared document; never paste a real key, a mailbox password or a credential-bearing connection string.

**Tenant-key uniqueness is enforced on the LeadBoost side.** If two LeadBoost organization IDs mapped to the same key, Mailer would resolve both requests to the same Mailer tenant, exposing mailbox or conversation data across those LeadBoost organizations; the client therefore refuses such a map (see above) and startup logs a warning with the reason code (`duplicate_key_value` / `duplicate_organization_entry`). This does **not** validate the Mailer's own `ORG_KEY_MAP` — that is a separate check on the Mailer side — and the two maps must still be exact inverses. Fixing a rejected map is an operator action; no secret is rotated automatically.

## 3. Mailbox lifecycle

A LeadBoost **email account** is the user's verified sender. A Mailer **mailbox** is the Mailer's own record of
that sender, used to send and to receive.

| Step | Where | What happens |
|---|---|---|
| 1 | LeadBoost | `POST /api/v2/organizations/{org}/email-accounts` creates the account; `…/{id}/verify` performs LeadBoost's own SMTP login test |
| 2 | LeadBoost | Any change that matters to the Mailer (verification outcome, disable, connection or credential change) sets `mailer_sync_state = pending` **in the same commit** |
| 3 | LeadBoost → Mailer | Reconcile. Wanted ACTIVE only when the account is active **and** VERIFIED; otherwise the Mailer mailbox is disabled. An unverified credential is never sent |
| 4 | Mailer | `POST /mailboxes` creates (always ACTIVE, credential sent once). On `409`, LeadBoost lists (`GET /mailboxes`) and **adopts** by email address, then activates via `PATCH /mailboxes/{ref}` |
| 5 | LeadBoost | Stores the Mailer reference; `mailer_sync_state = synced` |

* **Sync state** has exactly two persisted values, `pending` and `synced`. `pending` plus an error code means
  "tried and failed"; `pending` with no code means "not attempted yet". A crash, timeout or Mailer outage cannot
  leave a stale `synced`.
* **Safe error codes** when a reconcile does not converge: `mailer_not_configured`,
  `mailer_org_key_not_configured`, `mailer_insecure_transport`, `mailer_unreachable`, `mailer_timeout`,
  `mailer_rejected`, `mailer_invalid_response`, `mailer_unavailable`.
* **Retry/reconciliation:** verifying again, editing the account, or `POST …/email-accounts/{id}/mailer-sync`
  re-runs the reconcile. It is idempotent: create, adopt on `409`, activate.
* **Supported configuration:** STARTTLS only (`smtp_use_tls` is always `true`). Accounts with
  `security_mode = "tls"` (implicit TLS) are **not provisioned** (`unsupported_security_mode`).
* **Credential custody:** LeadBoost keeps its own encrypted copy (key: `EMAIL_CREDENTIAL_ENCRYPTION_KEY`) for
  its own verification; the Mailer stores an independent encrypted copy (key: `MAILBOX_ENCRYPTION_KEY`) and is
  the authoritative sending credential. Neither key ever crosses the wire, and credentials never appear in logs
  or responses.
* **Credential-bearing calls** are only `POST /mailboxes` and the `PATCH` that activates with new SMTP fields.
* **Inbound (IMAP) settings** are not sent by LeadBoost. They are set on the Mailer mailbox through the
  Mailer API with the organization's key (see Mailer L1 contract). Until they are present, that mailbox is skipped
  by the inbound poll.
* **No distributed transaction.** If LeadBoost disables an account and the Mailer disable call fails, the
  Mailer mailbox can stay ACTIVE while LeadBoost shows `pending`. LeadBoost refuses **new** dispatches in that
  window, but requests the Mailer already accepted can still be sent until a retry converges.

## 4. Outreach dispatch

### LeadBoost side: authorize

1. `POST /api/v2/outreach-actions` creates an action `pending_review` (or already approved in automatic mode,
   when the organization's policy has `automatic_sending_enabled`, default off; optional `daily_send_limit`).
   The idempotency key is supplied by the caller or derived deterministically from the authorization snapshot,
   so a repeated request returns the same action instead of a second one. Reusing a key for a *different*
   request ⇒ `idempotency_key_reused`.
2. `POST …/{id}/approve` and `…/cancel` move the action; `POST …/{id}/dispatch` hands it to the Mailer.
3. Preconditions, each a safe error code: lead found, sender found, **active + VERIFIED +
   `mailer_sync_state = synced`**, exactly one active sender (`multiple_active_senders`: the integrated path
   refuses to guess, before any claim), valid recipient, and a ready message snapshot (`message_not_ready`).
4. The claim `approved → dispatching` is a single atomic update, so concurrent dispatches have one winner.

### The call

```
POST {MAILING_AGENT_BASE_URL}/integrations/leadboost/outreach-requests
X-API-Key: <per-organization key>

{ "external_action_id": "<OutreachAction.id>",
  "idempotency_key":    "<OutreachAction.idempotency_key>",
  "correlation_id":     "<optional>",
  "recipient": { "email": "…", "name": "…", "title": "…", "company": "…" },
  "context":   { "value_proposition": "<organization description>", "recipient_facts": ["…"] } }

202 { "accepted": true, "mailing_agent_reference": "<ref>" }
```

The schema is strict (unknown fields ⇒ 422). **Deliberately excluded:** sender, SMTP host/user/password, any
credential, subject, body, tenant, mailbox reference, scores, qualification reasoning and other internal
intelligence. The Mailer uses the organization's **sole ACTIVE mailbox**; zero or several ⇒ `409`.

* `value_proposition` comes from the organization's description. If unset (or still the registration
  placeholder), the dispatch fails with `value_proposition_not_configured` and nothing is invented.
* `recipient_facts` are real lead fields only (industry, about text, founded year, employee band, revenue band).
* Success requires a 2xx **and** a boolean `accepted`; anything else is a failure.

### What "accepted" means

| Stage | Owner | Meaning |
|---|---|---|
| `submitted` (LeadBoost) | LeadBoost | The Mailer returned `202`: the request is **durably queued** (one commit). Nothing more |
| `queued` | Mailer | Waiting for generation or for a send claim (`generating` is internal and reads as `queued`) |
| claimed / `sending` | Mailer | A worker holds a lease and is attempting delivery |
| `sent` | Mailer | The **SMTP server accepted the message**. Not inbox delivery |
| `failed` | Mailer | A definite failure (LLM error, blocked draft, send-gate refusal, definite SMTP failure) |
| `unknown` | Mailer | The outcome is ambiguous: an error during or after the SMTP `DATA` phase, or an expired send lease. **Never auto-resent** |
| delivered | nobody | No component observes recipient-inbox delivery |

Generation and sending happen in the Mailer **worker**, never inside the HTTP request, on 15-second polls with
a 300-second generation lease and a 900-second send lease (defaults). An expired send lease resolves to
`unknown`, not back to `queued`, because a duplicate email is worse than an unconfirmed one.

### LeadBoost error mapping

A dispatch that is not accepted leaves the action `dispatch_failed` with one safe, closed code (never raw
exception or HTTP body text):

| Code | Cause |
|---|---|
| `mailing_agent_not_configured` | No base URL |
| `mailing_agent_insecure_transport` | URL is neither HTTPS nor loopback |
| `mailing_agent_auth_not_configured` | No key mapped for this organization |
| `mailing_agent_missing_idempotency_key` | Action has no key |
| `mailing_agent_unreachable` / `mailing_agent_timeout` | Connection failure / timeout (acceptance unknown) |
| `mailing_agent_rejected` | Non-2xx other than 409 (including auth failure) |
| `mailing_agent_conflict` | `409`: no or several ACTIVE mailboxes, or key reused for a different operation |
| `mailing_agent_invalid_response` | 2xx without a boolean `accepted`, or schema drift |
| `mailing_agent_unknown_error` | Anything else |

`dispatch_failed` is retry-eligible or cancellable. A retry sends the **same idempotency key**.

## 5. Conversation and delivery state (read-only)

| Layer | Endpoint |
|---|---|
| Browser → LeadBoost | `GET /api/v2/outreach-actions/{action_id}/mailer-state` |
| LeadBoost → Mailer | `GET /integrations/leadboost/outreach-actions/{idempotency_key}/conversation?limit=20` (1 to 50) |

* **Correlation.** Rooted on the action's idempotency key (percent-encoded, since keys may contain `/`). The
  Mailer tenant comes only from the API key. This still works if the dispatch response was lost.
* **A conversation is per recipient, not per action.** Several actions to the same address share one
  conversation; each outbound message carries its own dispatch's state.
* **State** is the dispatch's (`queued|sending|sent|failed|unknown`), never a message status. `unknown` is shown
  as "unconfirmed", with guidance not to resend.
* **Selection and bounds.** The most recent `limit` messages, oldest first, with `has_more`; bodies capped at
  20,000 characters (`body_truncated`); rendered as **plain text** because inbound text is third-party content.
* **Exclusions.** Outbound: only this organization's dispatches to this recipient. Inbound: only mail received
  through a **mailbox owned by this organization**. Inbound with no provable mailbox owner (the webhook and the
  legacy deployment-global IMAP poll) is excluded. Not exposed at all: database ids, RFC message headers,
  error text, intent/analysis fields, grounding, lease data, mailbox addresses, credentials, organization.
* **Strict parsing.** LeadBoost validates the response with closed models; any drift is
  `mailing_agent_invalid_response`.
* **LeadBoost's response is a product view**, with `availability` ∈ `available | not_dispatched |
  not_found_at_mailer | mailer_unavailable`, a closed `error_code` (only with `mailer_unavailable`), and a
  `mailer` block. Mailer-side 404, timeout, 5xx and bad schema are reported there with HTTP 200, never as a false
  success. HTTP 404 means only that the action is not in the caller's organization. The Mailer is not asked while
  `dispatch_attempts == 0`.
* **Why read-only, on both sides.** The endpoint takes no lock and makes no claim, retry, queue entry, SMTP/IMAP
  or LLM call. If LeadBoost says `dispatch_failed` and the Mailer says `sent`, **both are shown**. Healing from a
  GET would let a page view change money-adjacent state and would couple two owners' state machines. Any
  reconciliation is a separate, deliberate job.

### How replies get in

The Mailer worker polls each eligible mailbox with **its own IMAP settings and encrypted credential** (default
every 120 s): `UNSEEN` messages are fetched, processed in their own transaction, committed, and only then marked
Seen. Tenant and mailbox come from the polled row, never from message content. Correlation is organization- and
mailbox-scoped: RFC `In-Reply-To`/`References` against stored messages first; otherwise the sender address,
only if exactly one non-suppressed contact has it. Ambiguous or unmatched mail is **not persisted and not
guessed**. Persisted replies are deduplicated on `(mailbox, Message-ID)`, classified, and update contact state;
an unsubscribe request adds the contact to the organization's suppression list.

For contacts authorized through LeadBoost, the Mailer **does not** run its native automatic follow-ups or
auto-replies: replies are stored, classified and shown, and any further outbound email requires a new
LeadBoost-authorized action. (Native follow-up and auto-reply exist for Mailer-native campaigns only. This isolation is covered by the Mailer's
`tests/test_leadboost_native_isolation.py`: follow-up claims, forced follow-ups and reply-driven automation all exclude
LeadBoost-integration contacts, while unsubscribe still suppresses.)

## 6. Failure and consistency model

| Situation | What happens | What it does **not** guarantee |
|---|---|---|
| Timeout after the Mailer may have accepted | Action → `dispatch_failed` (`mailing_agent_timeout`) | LeadBoost cannot tell if the Mailer accepted |
| Retry with the same key | Mailer **replays** the original operation; no second dispatch | A replay of an already-`failed` Mailer dispatch stays failed; a new send needs a new operation (an explicit new idempotency key) |
| Mailer `409` | `mailing_agent_conflict`; nothing queued | |
| Auth failure / missing org key / missing tenant configuration | `mailing_agent_rejected` / `mailing_agent_auth_not_configured`; the Mailer returns 401 for a bad key and 503 when its key map is not configured | |
| Schema drift | `mailing_agent_invalid_response`; never passed through | |
| Mailbox sync failure | Account stays `pending` + error code; **new** dispatch refused | Requests already accepted may still send until convergence |
| SMTP ambiguous outcome | Dispatch `unknown`; never auto-resent | Neither "sent" nor "failed" is claimed |
| Mailer down during a read | `availability = mailer_unavailable`, HTTP 200 | No retry, no healing |
| LeadBoost crash between claim and Mailer call | Action can stay `dispatching`; there is no lease reclaim | Operator action is needed (see [`DEPLOYMENT.md`](DEPLOYMENT.md)) |
| Mailer-side `failed`/`unknown` after acceptance | Shown on read; LeadBoost action stays `submitted` | Not healed in LeadBoost |

**No exactly-once delivery.** Idempotency plus durable state prevent duplicate *dispatches* from retries.
External SMTP offers no such guarantee: a message may be handed over without the confirmation reaching the
worker, which is why that case is `unknown` and not retried. **No cross-service atomicity** exists; the
windows are listed above and are bounded by `mailer_sync_state` and the read-only reconciliation model.

## 7. Configuration cheat sheet

| Setting | Service | Purpose |
|---|---|---|
| `MAILING_AGENT_BASE_URL` | LeadBoost | Mailer base URL (HTTPS, or loopback HTTP) |
| `MAILING_AGENT_ORG_API_KEYS` | LeadBoost (secret) | `{org_id: key}` |
| `MAILING_AGENT_TIMEOUT_SECONDS` | LeadBoost | Per-call timeout, default 15 |
| `EMAIL_CREDENTIAL_ENCRYPTION_KEY` | LeadBoost (secret) | Encrypts LeadBoost's copy of the SMTP credential |
| `ORG_KEY_MAP` | Mailer (secret) | `{key: org_id}`; must agree with the LeadBoost map |
| `SYSTEM_OPERATOR_API_KEY` | Mailer (secret) | Operator routes only; distinct from tenant keys |
| `MAILBOX_ENCRYPTION_KEY` | Mailer API + worker (secret) | Encrypts the Mailer's mailbox credentials; identical on both processes |
| `LEADBOOST_INTEGRATION_SENDER_EMAIL` | Mailer | Must be set or the first dispatch for an organization returns 503; the actual `From` address comes from the mailbox, while the display name and sender organization come from deployment-wide settings |
| `LIVE_SENDING_ENABLED` | Mailer worker | Gates real SMTP sends |

Full variable lists: LeadBoost in [`ENVIRONMENT.md`](ENVIRONMENT.md), Mailer in its own README and the C13 runbook.

## 8. Known limitations

* **Snapshot ≠ delivered text.** The Mailer generates its own message; LeadBoost's snapshot is not
  synchronized and not claimed equivalent. A LeadBoost-side message must exist for an action to be created.
* **Display name and sender organization are deployment-wide** on the Mailer, not per customer.
* **Inbound provenance.** Webhook and legacy global IMAP mail is excluded from the LeadBoost view.
* **IMAP provisioning** is a Mailer-API step, not part of LeadBoost's account flow.
* **No implicit-TLS** mailboxes. **No automated tenant-key provisioning.**
* **Stranded `dispatching`** after a crash, and **no healing** of LeadBoost state from the Mailer.
* **Observability** across the two services is per-service logging; there is no shared trace id beyond the
  optional `correlation_id`.
