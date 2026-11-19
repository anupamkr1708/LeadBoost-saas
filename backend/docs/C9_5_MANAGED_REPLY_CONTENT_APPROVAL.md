# C9.5 — Managed replies: approval of the exact content

**Recorded:** 1 November 2026. **Bases:** LeadBoost `ba462eaf18c0d24a84435272d1a6e4c88bf9aecc`, Mailer
`a0f9454a74e1395e14d4deb510844db743559846` (both verified against the remotes before any edit).

The invariant this feature exists to uphold:

> **The bytes sent by SMTP must correspond to the exact message content the user approved. If that cannot be proven,
> the message must not be sent.**

The six sections below are kept apart on purpose, so that a reader can see exactly which claims rest on which kind of
evidence. In particular, **nothing here claims a real Gmail or company-inbox delivery** (section 6).

---

## 1. Previously verified: C9.4 backend integration (unchanged evidence, not re-claimed here)

C9.4 (LeadBoost PR #17, Mailer PR #27) added opaque inbound `message_reference` tokens, tenant-scoped reply-parent
resolution, RFC threading from the Mailer's stored parent, and the backend reply action (manual only, identity derived
from tenant + inbound reference, no caller idempotency key). Its contract is in `CONTRACT.md` section 3a. Those
behaviours are **preserved**; this work re-ran the relevant existing tests as regression (section 4), but it does not
re-verify C9.4 and nothing in C9.4 was weakened.

One C9.4 statement is **superseded**: C9.4 approved the *action* and the Mailer wrote the text afterwards, so the reviewer
never saw it. That no longer holds for replies (section 2).

## 2. New in C9.5: the exact-content approval workflow

```
inbound message ─ Reply ─► LeadBoost creates ONE reply action (pending_review; nothing sent, nothing generated)
                       ─► "Generate reply draft": Mailer accepts durably, generates, persists ONE draft Message,
                          and HOLDS it (ExternalDispatch AWAITING_APPROVAL — the SMTP worker cannot claim it)
                       ─► LeadBoost reads and shows the Mailer's persisted subject + body, with a digest
                       ─► "Approve exact draft": LeadBoost re-fetches the current draft and approves only if its
                          digest equals the one on screen; evidence stored atomically; NOTHING is sent
                       ─► "Send approved reply" (separate, explicit): the Mailer re-computes the digest from the
                          stored text, compares, and only then moves the SAME dispatch to the send queue
                       ─► the existing SMTP worker re-verifies the digest of the exact subject/body it is about
                          to transmit, then sends. Mismatch ⇒ no SMTP, FAILED, content-free reason.
```

**Mailer.** New internal states `AWAITING_APPROVAL` and `CANCELLED`; three additive columns on `external_dispatches`
(`content_approval_required`, `approved_content_sha256`, `approved_at`; migration 013). The held state is unreachable by
the send claim by construction (it claims only `state='queued'`). Release and cancel are each **one guarded `UPDATE`**,
so the database arbitrates races. Every request carrying `in_reply_to` is marked as requiring approval at acceptance, and
completing generation sets it again, so a reply accepted before the upgrade is still held. The legacy
`POST /messages/{id}/approve` still refuses every integration-managed message in every state. Details:
Mailer `docs/API_AND_INTEGRATION.md` section 5A.

**The digest (v1).** `sha256(json.dumps({"body","subject"}, sort_keys=True, separators=(",",":"), ensure_ascii=False)
.encode("utf-8"))`; a `None` subject hashes as `""`; **no normalization** (line endings and Unicode forms matter);
unencodable text fails closed; drafts above the preview limit (subject 998, body 20 000 characters) are neither
previewable nor releasable — never truncated. It covers the subject and body **text**; it does *not* cover headers the
Mailer generates at send time (From, Message-ID, Date) or MIME transfer encoding. `In-Reply-To`/`References` come from the
stored parent and are verified by tests rather than by the digest. LeadBoost treats the digest as opaque.

**LeadBoost.** `OutreachState` is unchanged. Three nullable columns on `outreach_actions` (Alembic `e5c9a3b7d214`):
`reply_draft_requested_at`, `approved_content_sha256`, `approved_draft_version`. Endpoints and error codes:
`CONTRACT.md` section 3b. A reply may now be created for a lead with **no** outreach text (empty snapshot; nothing is
invented). Cancel of a reply from `dispatch_failed` asks the Mailer **first**, because an ambiguous release may have
succeeded (see 3b).

**Security properties** (each is a test, section 4): foreign tenant ⇒ indistinguishable 404 at both services, no Mailer
traffic caused; the browser never receives a Mailer URL, key, database id or RFC header; stale/modified/cancelled/oversized
drafts fail closed; ordinary approve cannot approve a reply; the Mailer key never leaves the server.

## 3. Frontend implementation

Next.js 14 / TanStack Query 5, no new runtime dependency and no new state library.

* **Reply** appears only on an **inbound** message whose `message_reference` is exactly 32 lowercase hex characters; never
  on outbound messages, never when the reference is absent or malformed. The token is carried as data and never rendered.
  `MailerStatePanel` stays read-only: it gained only an optional `onReply` callback.
* `ReplyComposer` shows the message being answered, the recipient (from the lead), the sender mailbox, a status line, and
  the **full** persisted subject and body as **plain text** (React text nodes; no raw-HTML API — a backend test guards
  the directory). Stages: Creating reply, Generating reply, Draft ready for review, Generation failed, Approved,
  Ready to send, Sending, Sent (SMTP accepted), Failed, Delivery unconfirmed (do not resend), Cancelled.
* **Approve exact draft** is enabled only for a complete, fingerprinted draft on an action awaiting approval, and sends the
  digest of the text *on screen*. **Send approved reply** is enabled only after approval succeeded and its digest is stored.
  Both are disabled while any request is in flight, with a synchronous lock against double-clicks.
* Polling is bounded: only while generating or released-but-unsent, backoff 2 s → 15 s, at most 30 reads, then a manual
  "Check again". Reads retry only transient failures, twice.
* `OutreachCard` now excludes reply actions from the lead's "active action", so the ordinary Approve/Send controls can never
  act on a reply.
* Limitations: read-only preview (no edit/regenerate); one reply composer at a time; **a cancelled or failed reply cannot be
  retried for the same inbound message** (reply identity is tenant + inbound reference; re-creating returns the same
  cancelled action and starting a draft is refused). The sender shown for a new reply is the card's selected verified sender.

## 4. Synthetic CI verification (deterministic, offline, no live LLM, no real provider)

Everything below was run from **fresh clones of the two bases with the V2 patch series applied** (`git am`; trees identical to the
source branches), in **fresh virtualenvs** built from each `requirements.txt`, and the frontend under a real **Node v20.20.2** (CI's
version) from an **empty `node_modules`**. "Exit" is the real process exit code of the command shown. Python 3.12.3, pytest 7.4.4,
npm 10.9.7, PostgreSQL 16.15, Dovecot 2.3.21. Full detail: the patchset's `verification/TEST_REPORT.md`.

| Suite | Exit | Result |
|---|---|---|
| Mailer default suite (`pytest -q -p no:cacheprovider`) | 0 | 1612 passed, 3 skipped, 175 deselected |
| Mailer, all `-m postgres` tests on real PostgreSQL 16 (gate, migrations, races, claims, concurrency) | 0 | **149 passed**, 0 skipped, 0 failed |
| LeadBoost backend, CI's command (SQLite, no services) | 0 | 1422 passed, 35 skipped (service-gated) |
| LeadBoost backend, **all services live** (PG migrations + cross-service E2E against the pinned Mailer commit) | 0 | **1457 passed, 0 skipped, 0 failed** |
| Alembic CLI on fresh PG: `heads`, `upgrade head`, `check`, `downgrade -1`, `upgrade head`, `check` | 0 for each of the five with a captured exit code (`heads` was inspected: one `(head)` line) | single head `e5c9a3b7d214`; "No new upgrade operations detected" |
| Frontend `npm ci` (Node 20, empty `node_modules`) | 0 | succeeds; manifest and lockfile root agree; the five test packages are in `devDependencies` and none in `dependencies` |
| Frontend `npm run typecheck` / `npm run lint` | 0 / 0 | clean |
| Frontend `npm test` (vitest 2.1.9) | 0 | 108 passed (3 files) |
| Frontend `npm run build`, **repo as-is** | **1 (FAILED)** | `next/font` cannot reach Google Fonts from this sandbox (`ENOTFOUND`). Environmental, but it means the unmodified build was **not** demonstrated to pass here. |
| Frontend `npm run build`, repo **unmodified**, only the three Google Fonts network responses mocked (Next's `NEXT_FONT_GOOGLE_MOCKED_RESPONSES` hook) | 0 | compiled; lint/types validated; 17/17 pages; no source file changed. **This is not a real-network build** (no font was fetched); CI, which has network access, must still show the genuine pass. |

* **Baselines before any edit:** Mailer exit 0, 1514 passed, 5 skipped, 135 deselected. LeadBoost exit 0, 1343 passed, 31 skipped.
  Later changes in the Mailer's *deselected/skipped* counts come from installing Dovecot/PostgreSQL (conditionally collected
  integration tests), verified by collecting the untouched baseline commit in the final environment.
* **Tests that had to change, with reasons** (each is an intended contract change, none weakens a check for ordinary
  outreach): two Mailer test modules (conversation, reconciliation) that enumerated *every* dispatch state now enumerate the
  five *public* states (the new internal states are mapped, with new tests pinning the mapping); one Mailer column-set
  assertion; the PG-only wrapper step counts 14 → 15; the LeadBoost C9.4 test that approved a reply with no digest then
  dispatched it (that flow is exactly what was removed); the LeadBoost PostgreSQL migration tests that pinned the old Alembic head.
* **Mutation checks** (each protection disabled in turn; the suite must fail): Mailer — send-time digest gate, approval hold,
  release digest comparison, cancel restriction, **and (V2) the rollout gate's JSON predicate, its legacy-only filter, and its
  has-message distinction, plus the old downgrade guard**; PostgreSQL races — release state guard, cancel guard; LeadBoost —
  approve digest comparison, missing-approval guard, Mailer-first cancel, ordinary-approve refusal, release-instead-of-regenerate,
  **and (V2) the start endpoint's catch-all, legacy cancel contacting the Mailer, and oversized drafts collapsing into
  "unavailable"**; frontend — stored-digest requirement, reference validation, reply exclusion, post-release cancel withholding,
  **and (V2) legacy detection**; end to end against the real Mailer source — the send-time gate, the approval hold, **and (V2) a
  digest that strips the body and one that folds CRLF to LF**. One frontend mutant (dropping `hasCompleteDraft` from
  `canApprove`) is **equivalent** (the stage derivation already refuses an incomplete draft) and is kept as belt-and-suspenders.
* **Not run in CI-style mode:** the Mailer's `integration` tests that need a live LLM, real mail providers or `imap_e2e`.

## 5. Local provider verification (real protocols, disposable infrastructure)

`backend/tests/e2e/test_continuous_lifecycle_cross_service.py`, extended (not replaced), run against: the real LeadBoost
app; a **separate real Mailer process**; disposable PostgreSQL 16.15; a local SMTP sink speaking STARTTLS + AUTH; a
disposable Dovecot IMAP; a deterministic fake LLM. Command (from `backend`, Mailer checkout at the pinned commit):

```
MAILER_REPO_PATH=<mailer checkout> MAILER_PYTHON=<mailer venv python> POSTGRES_TEST_URL=<disposable db> \
C12_ALLOW_DESTRUCTIVE_TEST_DB=1 E2E_REQUIRE_CONTINUOUS=1 pytest tests/e2e/ -q       # exit 0 (part of the 1457 above)
```

It proves, against the live system: no SMTP before the draft is approved *and* explicitly sent (counted at the SMTP sink);
the real generation cycle lands in `AWAITING_APPROVAL`; the real SMTP worker, live sending ON, finds nothing to claim;
LeadBoost's displayed text equals the stored text and its digest equals an **independent recomputation** of the documented
algorithm; a foreign tenant is refused at LeadBoost *and* at the real Mailer; a stale digest is refused; approval alone
sends nothing; Send releases the *same* message (no new dispatch, no new message); the real dispatch sends exactly one
message whose subject/body fingerprint to the approved digest, with correct `In-Reply-To`/`References` and a distinct
Message-ID; readback is outbound → inbound → outbound. Attack scenarios: a **cancelled** draft cannot be approved, released
(even with a correct digest directly at the Mailer) or sent; content **altered after approval** is refused by the SMTP worker
(no SMTP attempted; the reason carries no content; not retried).

**Strict content assertions (V2).** The captured SMTP message is decoded exactly as a mail client would and compared for
**exact** equality of subject and body: there is no `strip()`, and the only normalisation is the transport's own CRLF → LF. This
applies to the initial outreach, the reply, and a Unicode scenario (non-ASCII RFC 2047 subject; a body with accented letters, CJK,
an emoji, a tab, interior trailing spaces and blank lines). The tamper scenario runs **six variants**, each of which the SMTP worker
must refuse with no SMTP attempted: a trailing space, a final newline, a leading space, a Cyrillic homoglyph, CRLF line endings
(which SMTP would silently normalise on the wire, so only the digest over the *stored* text can tell them apart), and replaced
content. Note that the generation pipeline itself strips a model's leading/trailing whitespace *before* a draft is persisted and
shown (`mailer_agent/llm/agent.py`), so a generated draft has no boundary whitespace to preserve; the boundary-whitespace risk is an
alteration *after* approval, which is exactly what the variants cover. Consequence of SMTP's CRLF normalisation, stated plainly:
a recipient cannot distinguish LF from CRLF line endings in the delivered text, whereas the digest does, so the digest is stricter
than what the wire can show.

Mutating the real Mailer source confirmed the test can fail: removing the send-time gate lets the altered text go out over real
SMTP; making the Mailer's digest `strip()` the body, or fold CRLF to LF, fails the test at precisely the `trailing-space` and
`crlf-line-endings` variants.

The driver gained one adversarial fixture, `tamper-message` (behind the same disposable-database guard as `reset-db`),
because the harness's "no writes to simulate an outcome" rule does not cover simulating a hostile writer.

**Real-PostgreSQL concurrency** (Mailer `tests/test_managed_reply_approval_postgres.py`, 6 tests): 25 release-vs-cancel races
and 12 concurrent identical releases each have exactly one winner; cancel versus the generation worker's fenced completion
cannot yield a sendable row; 8 concurrent SMTP claimers never see a held draft. **Alembic** on PG 16: single head
`e5c9a3b7d214`, fresh upgrade, `alembic check` (exit 0, "No new upgrade operations detected"), downgrade, reapply, and
survival of real pre-feature rows with NULL evidence.

## 6. Hosted real-provider verification — **NOT DONE**

No real Gmail, Google Workspace, Outlook or company-inbox mailbox was used. Everything in section 5 uses disposable local
SMTP/IMAP. Therefore **not verified:** delivery to a real inbox, provider-specific threading (Gmail's conversation
grouping), SPF/DKIM/DMARC outcomes, provider rate limits and rejection behaviour, real-provider IMAP quirks, and a deployed
staging environment. SMTP acceptance (`sent`) must not be read as inbox delivery, and the UI says so.

## Replies that predate this feature (C9.4)

See `CONTRACT.md` ("Replies that predate exact-content approval") for the per-state table, the read-only inventory SQL and
the rule: **an old approval is never reused for a new draft, and nothing is reset, deleted or migrated.** In short:
`pending_review` takes the new flow; `approved` / `dispatch_failed` / `dispatching` / `submitted` are labelled "legacy",
cannot be started, approved or sent, and can only be cancelled where LeadBoost already allowed it (a `dispatch_failed`
cancel asks the Mailer first and cancels only on proof that nothing was or can be sent; an existing legacy Mailer operation
is answered `409 legacy_reply_requires_reconciliation`, which LeadBoost never turns into a local cancel). Cancelling is
terminal for that inbound message in v1.

## Rollout

The first release blocks while any unresolved legacy managed reply exists; it does not migrate old approvals. Exact procedure
and the guarded SQL: Mailer `migrations/README.md`, "Legacy managed replies". In order:

1. **Pause LeadBoost managed-reply API traffic** at the edge (`503` + `Retry-After` for `POST /api/v2/outreach-actions` and
   `POST /api/v2/outreach-actions/{id}/(approve|dispatch|cancel|reply-draft)`; reads may stay), **drain old API requests** to
   zero in flight, and stop any process that can call the Mailer's `outreach-requests`.
2. Mailer: keep the **old** workers running and let them drain every legacy managed reply; run
   `scripts/migrate_preflight.py gate` until it exits 0 (it refuses, exit 4, while ANY legacy managed reply is queued -- with or
   without a generated message -- generating, or sending, and lists the rows). Resolve what does not drain (the README's table).
   Then **stop/drain the old Mailer workers**, re-run the gate, run migration 013 via the release wrapper, and **only then start
   the new workers**. Details and the JSON-correct inspection query: Mailer `docs/API_AND_INTEGRATION.md` §5A.
3. LeadBoost: run the read-only inventory above, run Alembic `e5c9a3b7d214`, then deploy backend and frontend, then lift the pause.
4. Between steps 2 and 3 a reply created by the *old* LeadBoost generates and sits held forever (fails closed; reads as
   `queued`); with the pause in place none is created. There was no reply UI before this change, so exposure is small.
5. After the Mailer PR merges, replace the CI pin with the Mailer **merge** commit SHA.

## Known limitations

* A cancelled or failed reply is terminal for that inbound message; no retry/regenerate/edit (needs versioned re-approval).
  This is **not implemented**.
* Held drafts never expire; there is no stale-draft sweep. **Not implemented.**
* **No browser-level test exists** (no Playwright/Cypress); the composer is covered by jsdom component tests only.
* **No real-mailbox test has been run** (section 6).
* The digest covers text, not generated headers or MIME encoding.
* `mailing_agent_reference` is now populated when generation starts (it was previously set only on submission). It is the
  Mailer's opaque public reference, already browser-visible after submission, and is not present on the new draft endpoint.
* `next build` cannot be verified in a network-restricted sandbox (it downloads Google Fonts in `layout.tsx`); CI is
  unaffected.
* Section 6 above.
