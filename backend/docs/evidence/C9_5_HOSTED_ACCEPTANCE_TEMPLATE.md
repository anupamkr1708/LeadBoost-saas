# C9.5 hosted acceptance record (blank template)

Fill this in **after** running `docs/C9_5_SMOKE_TEST.md` on the deployed system. Keep it free of personal data: redact addresses (`m1@...`, `m2@...`), no message bodies,
no tokens, no connection strings. Issue #20's last box ("smoke test passes in the deployed environment") is checked only from a completed copy of this record.

## 1. Run identity
| Field | Value |
|---|---|
| Date and time (UTC), operator of record | |
| Mailer commit deployed (SHA) / Render deploy id | |
| LeadBoost backend commit (SHA) / Railway deploy id | |
| LeadBoost frontend deployment (Vercel id) | |
| Mailer migrations applied (output of `migrate_release.sh --status`, summary) | |
| LeadBoost `alembic current` | |
| Mailer worker count during the run (must be 1) | |
| Dummy organisation id / dummy lead id | |

## 2. Controlled live sending
| Field | Value |
|---|---|
| Worker `LIVE_SENDING_ENABLED=true` set at / reverted to `false` at | |
| Anything approved-but-unsent when it was reverted? (must be no) | |
| Recipients: only mailbox 1 and mailbox 2? (yes/no) | |

## 3. State transitions (one row per observed state, with time)
| Step | LeadBoost action state | draft state | Mailer delivery state | UI wording seen | Time |
|---|---|---|---|---|---|
| reply created | | | | | |
| draft generated | | | | | |
| approved | | | | | |
| sent | | | | | |
| cancel path: cancelled | | | | | |

## 4. Identifiers and headers (redacted)
| Item | Value |
|---|---|
| Action id / idempotency key prefix (first 12 chars) | |
| Approved digest prefix (12 chars) / digest the verifier printed | |
| `Message-ID` of the inbound from mailbox 2 (step 3) | |
| `Message-ID`, `In-Reply-To`, `References` of the received reply (step 10) | |
| `In-Reply-To` equals the inbound's `Message-ID`? | |
| Received body identical to the text read at step 6? | |
| Second inbound reply (step 11) appeared in the conversation panel? | |
| Mailbox 2 received exactly one copy? Nothing from the cancelled draft? | |
| SPF / DKIM / DMARC results from the received headers (if sending domain is live) | |

## 5. Verifier output
Paste the output of `c95_smoke_verify.py` (it contains no bodies or subjects) and its exit code:
```
(paste)
```
Operator SQL result (the query in the smoke-test document) for the sent and the cancelled action:
```
(paste)
```

## 6. Screenshots (list; do not embed any that show real addresses)
| # | What it shows | File name |
|---|---|---|

## 7. Earlier local manual run (dummy company, two own mailboxes; **not hosted**)
Not recorded as evidence yet. Fill in only what you can supply; leave blank otherwise.
| Field | Value |
|---|---|
| Date, repositories/commits used | |
| What was exercised | |
| Where the notes/screenshots are | |

## 8. Outcome
| Question | Answer |
|---|---|
| Every blocking assertion of the verifier held? | |
| Anything unexpected (list)? | |
| Rollback needed? Decision taken and by whom | |
| Issue #20 box "smoke test passes in the deployed environment" | checked / not checked |
