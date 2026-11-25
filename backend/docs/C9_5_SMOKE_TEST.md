# C9.5 hosted smoke test (dummy organisation, your own two mailboxes)

Purpose: prove, on the **deployed** system, the full managed-reply flow: create reply -> generate draft -> read the exact text ->
approve that exact draft -> send -> inbound threading -> and the cancel path. It is the acceptance step of issue #20
("smoke test passes in the deployed environment"). It is run **after** `C9_5_ROLLOUT_RUNBOOK.md` stage 7.

**Hard rules**
- Mail goes only to **mailbox 1** (the sender) and **mailbox 2** (the recipient), both yours. Never a customer, never a real lead.
- Use a **dummy organisation** and a dummy lead whose email is mailbox 2.
- Nothing in this document needs production data, and nothing in it may be run against a customer's organisation.
- The verifier script is read-only and never sends mail. Credentials come from environment variables only; never paste them anywhere.

## 0. Before you start

| Need | How to check |
|---|---|
| Rollout stages 0-7 done; backend and frontend deployed | the runbook's checklist |
| Dummy organisation + user exist in LeadBoost; a dummy lead with your **mailbox 2** address | LeadBoost UI |
| Mailbox 1 connected the way the product does it: add the sender account, **verify** it, **sync** it to the Mailing Agent | `[REPO: backend/api/endpoints/email_accounts.py]` routes `POST .../email-accounts`, `/verify`, `/mailer-sync`; menu labels: check the UI |
| The Mailer is configured with a **recipient-safe** state: `LIVE_SENDING_ENABLED=false` on both Mailer services, `AUTO_REPLY_ENABLED=false` | your Render / Railway dashboards |
| `LEADBOOST_INTEGRATION_SENDER_EMAIL` is set on the Mailer API (the first dispatch per organisation returns 503 while empty) | `[REPO: render.yaml]` |
| Inbound polling for mailbox 1 works | operator `GET /system/metrics/inbound` shows recent poll cycles `[needs the PR-4 code deployed]` |

### The live-sending switch, and the trap in it
Sending is off by default. The acceptance needs **one** real send, so you turn `LIVE_SENDING_ENABLED=true` on the **Mailer worker only**
`[REPO: docs/DEPLOYMENT.md item 6]`, and turn it back afterwards.

> **`false` is not a pause.** `[REPO: docs/DEPLOYMENT.md section 6]` With the flag false the worker does not hold dispatches: a claimed
> dispatch is **failed** before SMTP with `live_sending_disabled`. So (a) do not turn the flag back to `false` while anything of yours is
> approved-but-unsent, and (b) if you need to stop, **suspend the worker service** instead (SIGTERM drains up to 25 s; in-flight
> work becomes `UNKNOWN`, the rest stays `QUEUED`). The flag is a separate copy on each service: change it on the worker, not on the API.

Order of switching: `false` -> (steps 1-5 below up to "Send approved reply") -> set the worker to `true` and redeploy/restart it -> send -> confirm `sent`
-> set it back to `false` immediately -> continue with steps 8-10 (they need no live send, except step 1 which is done first, see the note there).

## 1. The steps

Each row: what you do, what you must see (UI wording is from `reply-state.ts` / `reply-composer.tsx`), the LeadBoost action state, the Mailer delivery state.
`LB state` is `GET /api/v2/outreach-actions/{id}` `.state`; `draft` is `GET .../reply-draft` `.state`.

| # | You do | Expected UI | LB state / draft | Notes |
|---|---|---|---|---|
| 1 | Worker `LIVE_SENDING_ENABLED=true`. In the dummy org, create and approve and send one **ordinary first-touch** outreach to the dummy lead (mailbox 2). This is the message mailbox 2 will answer. | the ordinary outreach card reaches its sent state | `submitted` | This is a live send to your own mailbox. If you prefer to avoid a second live send, send a normal mail from mailbox 1 to mailbox 2 by hand and reply to it: the Mailer matches the reply by `In-Reply-To`; the conversation panel then has the inbound but no outbound twin. |
| 2 | Set the worker flag back to `false`. | | | Nothing of yours is queued now (step 1 reached `sent`). |
| 3 | From **mailbox 2**, reply to that message with a short, harmless text. | | | |
| 4 | Wait for the Mailer's inbound poll; open the lead's conversation panel. | the inbound message appears with a **Reply** button | | Never present without a valid message reference. |
| 5 | Click **Reply**, choose the sender account, click **Generate reply draft**. | `Creating reply` -> `Generating reply` -> `Draft ready for review` | action `pending_review`; draft `generating` -> `awaiting_approval` | Nothing is sent. The draft is **held** in the Mailer (`AWAITING_APPROVAL`). |
| 6 | **Read the exact subject and body on screen.** Note the first characters. | the text and a content fingerprint | draft has `content_sha256` | This is the text that will be sent. |
| 7 | Click **Approve exact draft**. | `Approved`; the **Send approved reply** button appears | action `approved`, `approved_draft_version` = 1, `approved_content_sha256` set | **Nothing is sent by approving.** |
| 8 | Worker `LIVE_SENDING_ENABLED=true` (restart it). Click **Send approved reply**. | `Ready to send` -> `Sending` -> `Sent (SMTP accepted)` | action `submitted`; draft `sent`; Mailer delivery `sent` | "Sent" means SMTP accepted the message, not inbox delivery. |
| 9 | Set the worker flag back to `false` **now**. | | | See the trap above. |
| 10 | In **mailbox 2**: confirm receipt. Open the raw headers: record `Message-ID`, `In-Reply-To` (must equal the Message-ID of the message mailbox 2 sent in step 3), `References`. Compare the body with step 6. | the text is exactly what you read | | Not provable via API. |
| 11 | From **mailbox 2**, reply once more. Wait for the poll. | a second inbound message in the conversation panel | | Confirms inbound threading on the sent reply. |
| 12 | **Cancel path.** From mailbox 2 send one more message, wait, click **Reply** on it, **Generate reply draft**, read it, then click **Cancel reply** (do **not** approve). | `Cancelled` | action `cancelled`; draft `cancelled` | Mailbox 2 must receive nothing. |
| 13 | Run the verifier (section 2). | | | |

Do **not** test "approve, then change the draft" here: V1 has no edit or regenerate (known limitation, issue #21). A stale approval can only be cancelled.

## 2. The read-only verifier

```
export LB_BASE_URL=https://<your LeadBoost API origin>
export LB_EMAIL=...   LB_PASSWORD=...        # or LB_ACCESS_TOKEN=...   (type them in your shell; never in a file)
# optional, adds the Mailer's own view:
export MAILER_BASE_URL=https://<your Mailer API origin>   MAILER_TENANT_KEY=...
python backend/scripts/c95_smoke_verify.py --sent-action <id of step 8's action> --cancelled-action <id of step 12's action> --expect-inbound-replies 2
```
Exit 0 = every blocking assertion held; 1 = one failed; 2 = usage; 3 = transport. It prints states, counts, and 12-character digest prefixes; it never prints a subject or body.

| It asserts | Through |
|---|---|
| the sent action is `submitted`, approved by a user, version 1, with an approved digest | `GET /api/v2/outreach-actions/{id}` |
| the draft is `sent`, its digest equals the approved digest, and the digest **recomputes** from the subject and body the Mailer returned | `GET .../reply-draft` |
| Mailer delivery state `sent`; **exactly one** outbound message in the conversation has the approved digest and is `sent` | `GET .../mailer-state` |
| (with Mailer credentials) exactly one outbound message carries that action's dispatch reference and is `sent`; the Mailer's own draft read says `sent` with the same digest | Mailer `.../outreach-actions/{key}/conversation`, `.../reply-drafts/{key}` |
| the cancelled action is `cancelled`, never approved, its draft is `cancelled`, and **no** `sent` message carries its digest | same reads |

**Not provable via the API** (a green run does not cover these): the bytes handed to SMTP; the RFC headers; receipt in mailbox 2; SPF/DKIM/DMARC results.
Use step 10 (raw headers) and this **operator query** on the Mailer database, read-only, run by you:
```sql
SET default_transaction_read_only = on;
SELECT d.state, d.content_approval_required, left(d.approved_content_sha256, 12) AS approved_digest12, d.approved_at,
       m.status, m.message_id_header, m.in_reply_to_header, m.references_header
  FROM external_dispatches d LEFT JOIN messages m ON m.id = d.message_id
 WHERE d.idempotency_key = '<the action''s idempotency_key: reply:...>';
```
Expected for the sent reply: `state = sent`, `content_approval_required = true`, a digest prefix equal to the one the verifier printed, `in_reply_to_header` = the Message-ID of the inbound from step 3.
For the cancelled one: `state = cancelled`, no `message_id_header` (it was never sent).

## 3. Stop conditions

| You see | Do |
|---|---|
| step 5 stays on `Generating reply` more than a few minutes | check the Mailer worker (`/system/metrics/worker`, logs); LLM key; do not click again, it is idempotent but waits for the worker |
| `Draft unavailable` | Mailer unreachable or schema problem: check `/health`; stop |
| step 8 ends in `Failed` / `Not sent yet` | read the content-free reason in the worker logs; do not turn the flag to `false` until the dispatch is resolved |
| `Delivery unconfirmed` (`unknown`) | **do not resend**. Reconcile against mailbox 1's Sent folder by Message-ID |
| mailbox 2 received **two** copies, or anything from the cancelled draft | **stop everything**: suspend the Mailer worker, keep the evidence, report it as an incident |

Then fill `docs/evidence/C9_5_HOSTED_ACCEPTANCE_TEMPLATE.md`. Issue #20's last box is checked only from that record.
