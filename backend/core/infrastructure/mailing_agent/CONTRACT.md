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
* **Superseded by C9.5 (section 3b): approval of a reply is now of the exact generated text.** C9.4 approved the
  action and the Mailer wrote the text afterwards, so the reviewer never saw it. That behaviour no longer exists for
  replies: `approve` now requires the digest of a draft the reviewer was shown, and `dispatch` releases that same
  draft. The action's `subject`/`body` snapshot remains for ordinary outreach and is **not** the reply (a reply for a
  lead with no outreach text is created with `subject = null, body = ""`; nothing is invented).

## 3b. Exact-content approval of a managed reply (C9.5)

```
create (pending_review) -> POST .../reply-draft (Mailer generates + HOLDS) -> GET .../reply-draft (exact text)
  -> POST .../approve {content_sha256} -> POST .../dispatch (explicit; releases the SAME draft) -> Mailer sends
```

LeadBoost's `OutreachState` machine is unchanged. The draft's own status is read from the Mailer on demand.

| LeadBoost endpoint | Behaviour |
|---|---|
| `POST /api/v2/outreach-actions/{id}/reply-draft` | Asks the Mailer to **generate** the draft: the existing generated-outreach request under the action's own idempotency key, so a retry (e.g. after a timeout) replays the same Mailer operation instead of creating another. Sends nothing; the Mailer holds every managed reply. Returns the draft state. |
| `GET  /api/v2/outreach-actions/{id}/reply-draft` | Read-only, `Cache-Control: no-store`. The Mailer's persisted `{state, subject, body, content_sha256, draft_version, updated_at}`, fetched server-to-server and validated against a strict schema. States: `not_started, generating, awaiting_approval, queued, sending, sent, failed, unknown, cancelled`, plus `legacy` (a reply created before exact-content review existed: nothing to fetch). |
| `POST /api/v2/outreach-actions/{id}/approve` | **A reply requires** `{"content_sha256": "<64 lowercase hex>"}`. The digest is a *claim* ("this is what I saw"), never proof: LeadBoost re-fetches the Mailer's current draft and approves only if it is `awaiting_approval` **and** carries that digest. Approval and its evidence (`approved_content_sha256`, `approved_draft_version`, user, time) are written by the **same** atomic `UPDATE` that moves the action to `approved`. Ordinary actions take no body and are unchanged; the ordinary `approve_action` refuses a reply outright. |
| `POST /api/v2/outreach-actions/{id}/dispatch` | A reply needs the stored approval (`reply_approval_missing` otherwise, before any claim). It keeps the atomic `dispatching` claim and the pause / sender / mailbox gates, then **releases** the same draft with the *approved* digest. It never submits a generation request. |
| `POST /api/v2/outreach-actions/{id}/cancel` | LeadBoost's atomic cancel plus revocation of the held draft. See below. |

Mailer endpoints used (client: `reply_draft_client.py`; strict models, closed error vocabulary, no raw text or draft
content ever logged): `GET /integrations/leadboost/reply-drafts/{key}`, `POST .../release`, `POST .../cancel`.

* **Mailer 409 reasons passed through (closed set, anything else collapses to a generic conflict):** `draft_*` codes and
  `legacy_reply_requires_reconciliation`.
* **Error codes (closed):** `reply_draft_not_ready` (409), `reply_draft_stale` (409), `reply_approval_missing` (409),
  `not_a_reply` (409), `reply_content_hash_required` (422), `reply_draft_unavailable` (502: transport, timeout, 5xx, schema
  drift, or the Mailer rejecting the key), `reply_draft_too_large` (422: a draft the Mailer refuses to show in full, which
  can never be approved -- deliberately distinct from an outage, because retrying cannot help).
* **`POST .../reply-draft` reports real failures.** After the Mailer accepts the request the immediate read-back may fail.
  Only "the Mailer has no draft to show yet" is reported as `generating`; an outage, malformed Mailer response, oversized
  draft or rejected key are returned as their own errors. Nothing is lost by that: the request is recorded and repeating it
  is idempotent.

### Replies that predate exact-content approval (C9.4)

A C9.4 reply was approved as an *action* and its text was written by the Mailer afterwards, so it carries no reviewed
draft and no approved digest. The migration (`e5c9a3b7d214`) adds nullable columns and **touches no existing row**. The new
code handles each state of such a reply explicitly and never reuses an old approval for a new draft:

| C9.4 state | Identified as | What the new code does | Operator action |
|---|---|---|---|
| `pending_review` | not legacy (it approved nothing) | Takes the **new flow**; its stored outreach snapshot is neither shown nor sent. | None. |
| `approved` | legacy (`reply_draft_requested_at` NULL) | Refuses start / approve / dispatch with a clear message (`reply_approval_missing` on dispatch). Draft read returns `state: "legacy"`. **Cancel** works (LeadBoost-only: nothing was ever dispatched). | Leave it, or cancel it. Cancelling is **terminal for that inbound message** (see limitations). |
| `dispatching` | legacy | Refused as above; not cancellable (it left LeadBoost). | Wait for it to resolve; investigate if stuck. |
| `dispatch_failed` | legacy | Refused as above. **Cancel asks the Mailer first** and cancels only on proof: it revokes a held draft if one exists; cancels if the Mailer says the operation is `failed` or `cancelled` (nothing was or can be sent) **or genuinely has none (a real 404)**; **refuses** if the Mailer says it was already released, answers `409 legacy_reply_requires_reconciliation` (the operation exists and may be queued, sending, sent or ambiguous), or cannot confirm. On any refusal the action keeps its state, history and approval snapshot. | Reconcile the Mailer operation first (inventory below). A `legacy_reply_requires_reconciliation` refusal means an operator must establish what the Mailer did; it is never resolved by cancelling here. |
| `submitted` | legacy | Refused as above; not cancellable. Whatever was sent appears in the conversation. | None. |

Nothing is reset, deleted, re-keyed or auto-approved, and no approval is migrated: an old approval is not evidence about any
text, so it cannot be turned into approval of a new draft. Read-only inventory (run before rollout and after applying the
migration in a disposable copy):

```sql
-- LeadBoost: how many C9.4 reply actions exist, by state, and how many carry the new evidence (should be 0 before rollout)
SELECT state,
       COUNT(*)                                                    AS actions,
       COUNT(*) FILTER (WHERE approved_content_sha256 IS NOT NULL) AS with_digest,
       COUNT(*) FILTER (WHERE reply_draft_requested_at IS NOT NULL) AS draft_requested
  FROM outreach_actions
 WHERE reply_to_message_reference IS NOT NULL
 GROUP BY state ORDER BY state;
```

The Mailer-side counterpart is the rollout gate (`scripts/migrate_preflight.py gate`), which refuses while ANY unresolved legacy managed-reply
dispatch exists -- queued (with or without a generated message), generating, or sending -- so no old action can be left whose Mailer
operation the new worker would turn into a draft this UI cannot review or release (Mailer `migrations/README.md`, "Legacy managed replies"). The browser never receives Mailer text, URLs, keys or database ids.
* **Ambiguous release.** A timeout or 5xx on release leaves `dispatch_failed`; repeating dispatch re-sends the same
  release, which the Mailer treats idempotently for the same digest, so it either replays as already released
  (`submitted`) or releases now. It cannot double-send.
* **Cancel of a reply.** From `pending_review`/`approved`, LeadBoost's atomic cancel is the arbiter and the Mailer
  draft is then revoked (best effort: even if that call fails, LeadBoost never dispatches a `cancelled` action, so the
  draft cannot be released). From `dispatch_failed` a release may have **succeeded despite a lost response**, so the
  Mailer is asked to revoke **first**: if it was already released the cancel is refused (`invalid_state_transition`)
  rather than reporting "cancelled" while the email goes out; if the Mailer cannot be reached the cancel is refused.
  The same Mailer-first rule applies to any legacy (C9.4) reply that was dispatched. **A `404` means only "the Mailer has no such
  operation".** A legacy operation that exists is answered `409 legacy_reply_requires_reconciliation` (never `404`, whatever its
  state), and LeadBoost cancels only when the Mailer answers `cancelled`/`failed` (proof nothing was or can be sent) or a genuine
  `404`. An `ok` carrying any other state is not trusted.
* **Honest delivery states.** `sent` means SMTP accepted the message, not that it reached an inbox; `unknown` means the
  outcome could not be established and the message is never resent automatically.

## Known limitations (deliberate, documented — not hidden)
* **A cancelled or failed reply is terminal for that inbound message (C9.5, v1).** Reply identity is tenant + inbound
  reference, so creating a reply again returns the same action (`201`, same id, still `cancelled`) and starting a draft
  is refused (`409 invalid_state_transition`). Retrying or regenerating needs versioned drafts and re-approval and was
  deliberately not built. The draft is read-only: no editing and no regeneration, so `draft_version` is always `1`.
* **Held drafts do not expire.** A draft waits in the Mailer until it is released or cancelled.
* **Snapshot ≠ delivered text.** `OutreachAction.subject/body` is LeadBoost's *legacy snapshot* (what was
  authorized/reviewed). The integrated path delivers the **Mailer's own generated `Message`**. The two are
  not synchronized and not claimed equivalent; `Lead.outreach_message` and the MessagingAgent are untouched.
  Human review of the Mailer's generated text is deferred **for ordinary outreach**; for replies it is done (section 3b).
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
