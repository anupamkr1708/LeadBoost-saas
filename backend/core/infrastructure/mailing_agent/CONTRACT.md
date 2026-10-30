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
* LeadBoost holds **one distinct key per LeadBoost organization**:
  `MAILING_AGENT_ORG_API_KEYS={"<leadboost_org_id>":"<mailer_api_key>"}`. No key for the org ⇒
  fail closed (`mailing_agent_auth_not_configured`). Duplicate values are not rejected by the
  current client, so operators must ensure every LeadBoost organization maps to a unique Mailer
  API key; a duplicate can cause multiple LeadBoost organizations to authenticate as the same
  Mailer tenant. `MAILING_AGENT_API_KEY` is no longer read.
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

## 3. Delivery state and conversation — read-only (C9.3)

LeadBoost authorizes; the Mailer generates, sends, receives and owns the communication record. So that the
dashboard can show the **actual** message and what happened to it (rather than LeadBoost's own snapshot), the
LeadBoost **backend** reads one Mailer endpoint. The browser never does: it calls only LeadBoost's
`GET /api/v2/outreach-actions/{action_id}/mailer-state`, and never receives the Mailer's URL, key, key map, paths,
schema or any Mailer reference -- with ONE documented exception: the opaque reply handle on inbound messages (3a).

```
Mailer   GET /integrations/leadboost/outreach-actions/{idempotency_key}/conversation?limit=20   (1..50)
LeadBoost GET /api/v2/outreach-actions/{action_id}/mailer-state      (customer-facing; same auth/org rules as the other outreach routes)
```

* **Rooted on the action's `idempotency_key`** (the same key dispatch forwarded; unique per organization; still works if the
  dispatch response was lost). The Mailer tenant comes only from the API key — nothing in the request names one. The key is
  percent-encoded with `safe=""` (keys are caller-suppliable and may contain `/`).
* **A conversation is per recipient, not per action.** There is no Thread. It is the recipient's Contact and its messages, so
  several actions to the same address share one conversation; each outbound message carries *its own* dispatch's state.
* **State is the dispatch's.** `action.state` and each outbound `delivery_state` are `ExternalDispatch.state`
  (`queued|sending|sent|failed|unknown`; the Mailer's internal `generating` reads as `queued`). `unknown` means *delivery
  unconfirmed* — not failure; it is never translated and the UI says not to resend. `Message.status` is never consulted.
* **Which messages.** Outbound: those produced by this organization's dispatches for this recipient. Inbound: only rows
  received through a **mailbox that belongs to this organization**. Inbound with no provable mailbox owner (the webhook and
  the legacy deployment-global IMAP poll) is **excluded**; closing that provenance gap is separate security work.
* **Bounded.** The most recent `limit` messages, oldest first, `has_more` when older ones exist. No cursor. Each body is capped
  at 20 000 characters (`body_truncated`). Inbound text is third-party-controlled: it is data, rendered as **plain text**.
* **Not exposed by the Mailer:** database ids, RFC `Message-ID`/`In-Reply-To`/`References`, `error_message`, intent or
  analysis fields, grounding, claim/lease data, mailbox addresses or credentials, the organization.
* **Strict on our side.** The response is parsed with closed models (`extra="forbid"`, Literal enums, strict scalars,
  bounded sizes, "outbound ⇔ has `delivery_state`"). Any drift is `mailing_agent_invalid_response` — never passed through.
* **The LeadBoost response** is a product view, not a pass-through: `availability`
  (`available | not_dispatched | not_found_at_mailer | mailer_unavailable`), a closed `error_code` (only with
  `mailer_unavailable`) and `mailer` (delivery state, `updated_at`, messages, `has_more`). The Mailer's dispatch reference and mailbox
  references are dropped; an inbound message's opaque `message_reference` is kept (3a). Mailer-side 404 / timeout / 5xx / bad schema are reported there with HTTP 200 — never as a false
  success and never as raw error text. HTTP 404 means only that the LeadBoost action is not in the caller's organization.
  The Mailer is not asked while `dispatch_attempts == 0` (`not_dispatched`).
* **Read-only, both sides.** No write, lock, claim, retry, queue item, SMTP/IMAP or LLM call. LeadBoost never changes an
  `OutreachAction` in response: if LeadBoost says `DISPATCH_FAILED` and the Mailer says `sent`, **both are shown** and
  nothing is healed (that is a deliberate, separate, later reconciliation concern).

## 3a. Replying to an inbound message (C9.4)

A reply is an ordinary LeadBoost action (create -> approve -> dispatch) that additionally names the
inbound Mailer message it answers. There is no automatic reply: inbound mail is still only stored and
classified; a further email exists only because a person created, approved and dispatched an action.

* **Reference.** The conversation view (section 3) carries `message_reference` on **inbound** messages only
  (`null` on outbound). It is an **opaque token**: 32 lowercase hex characters (`^[0-9a-f]{32}$`), random,
  persisted once per message and never regenerated. It is not derived from, and never contains, an internal
  database id; **internal database ids are never exposed**, and neither are RFC Message-ID / In-Reply-To /
  References values. The Mailer's response is rejected as `mailing_agent_invalid_response` if a reference
  does not match that format.
* **Browser exposure (decision).** The browser must say which inbound message it is answering, so this token
  is passed through the mailer-state view as a *documented, non-secret reply handle*. It is not a bearer
  capability: it grants nothing by itself. Every use is resolved by the Mailer tenant-, mailbox-owner- and
  direction-scoped, so a token that is unknown, outbound, another organization's or from an unowned mailbox
  is an indistinguishable 404. LeadBoost additionally only ever stores it in the caller's own organization's
  action and derives the reply identity from (tenant, token). Keeping the token server-side behind a
  LeadBoost-issued handle was considered and rejected: it adds a table and a mapping to hide a value that is
  already non-guessable and tenant-enforced. Residual: LeadBoost does not check a token against the Mailer
  when the action is created, so a forged or foreign token surfaces as a Mailer 404 at dispatch, after approval.
* **LeadBoost.** `POST /api/v2/outreach-actions` accepts `reply_to_message_reference`. Enforced in the service
  layer: `mode` must be `manual` (a reply is always created `pending_review`; `automatic` is refused with
  `reply_requires_review`, whatever the organization's automatic policy says) and no `idempotency_key` may be
  supplied (`reply_idempotency_key_not_allowed`). The idempotency identity is **tenant + inbound reference**
  only, backed by the existing `UNIQUE(organization_id, idempotency_key)` constraint, so a retry, a re-poll
  or a different sender/lead can never create a second independently dispatchable reply (a different
  sender/lead for the same inbound message is a 409).
* **Mailer request.** The existing generated-outreach request gains an optional
  `in_reply_to: {"message_reference": "<32 lowercase hex>"}`. The Mailer derives subject (deterministic `Re:`),
  `In-Reply-To` (the inbound message's Message-ID) and `References` from its own stored row; none of these are
  accepted from the caller. The recipient must be the parent's sender; a contact with a native schedule is
  refused; the existing grounding and suppression gates apply unchanged.
* **Not a preview.** As for all generated outreach, the action's `body` is LeadBoost's snapshot and the Mailer
  writes the actual text **after** approval. Approving a reply authorizes the action; it does **not** show the
  reviewer the exact body that will be sent. The sent text is visible afterwards in the conversation view.

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
* Deferred: implicit TLS (`SMTP_SSL`), automated tenant-key provisioning, `display_name` in the Mailer, closing the
  inbound provenance gap (webhook and legacy global poll), removing LeadBoost's own encrypted credential; **healing** LeadBoost's `DISPATCH_FAILED` from the Mailer's
  state (C9.3 only *displays* it).

## Inbound mail (IMAP) — current behaviour

An earlier revision of this contract listed IMAP / inbound (M3) as deferred. That entry is **historical**: the Mailer
now receives replies itself.

* **Where it runs.** The Mailer worker polls each ACTIVE mailbox that has a complete IMAP configuration (default every
  120 s), using that mailbox's own encrypted credential. Nothing falls back to a deployment-global identity.
* **How it is configured.** IMAP settings are an all-or-none set on the Mailer mailbox (`imap_host`, `imap_port`,
  `imap_username`, `imap_password`), written through the Mailer API with the organization's key (see the Mailer's
  `LEADBOOST_L1_MAILBOX_CONTRACT.md`). **LeadBoost never sends IMAP settings** and does not read the mailbox; a mailbox
  without them is skipped by the poll.
* **Tenant binding.** Tenant and mailbox come from the polled row, never from message content. Replies are correlated
  within that organization and mailbox (RFC `In-Reply-To`/`References` first, otherwise a unique sender address);
  ambiguous or unmatched mail is not persisted.
* **What LeadBoost sees.** Only the read-only conversation view in section 3, restricted to mail received
  through a mailbox owned by the organization. For LeadBoost-authorized contacts the Mailer stores and classifies
  replies but does not send automatic follow-ups or auto-replies; a further email needs a new authorized action.
* **Still outside the view.** Mail that arrived by webhook or through the legacy deployment-global poll has no provable
  mailbox owner and stays excluded.
