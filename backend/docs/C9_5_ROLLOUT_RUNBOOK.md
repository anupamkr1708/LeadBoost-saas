# C9.5 rollout and rollback runbook

Scope: put the merged C9.5 "managed reply exact-content approval" release (Mailer `main` after PR #30, LeadBoost `main`
after PR #22) into production, in a controlled order, and be able to undo it. Nothing here changes behaviour: it is
procedure, evidence and decisions. Read it top to bottom once before the maintenance window.

**Evidence labels used below** (so you can tell what is known from what is assumed):

| Label | Meaning |
|---|---|
| `[REPO]` | read from a file in one of the two repositories (path given) |
| `[REHEARSED]` | observed in a rehearsal on a throw-away PostgreSQL 16 with synthetic data (log: `docs/evidence/ROLLOUT_REHEARSAL_RESULTS.md`); **not** observed on your hosted systems |
| `[FACT: X]` | supplied by the owner from a dashboard the repositories cannot show |
| `[UNKNOWN: check X]` | nobody here can see it; the step says where to look |
| `[DECISION]` | the owner must choose; this runbook does not choose |

---

## 1. Go / no-go checklist (one page)

Do not start the window until every line is answered "yes". Any "no" is a stop, not a warning.

**Evidence**
- [ ] The restored-dump rehearsal (LeadBoost) has been run and its results file is filled (`ROLLOUT_REHEARSAL_RESULTS.md`), and the adoption path in section 5 (D4) is chosen from what the rehearsal showed.
- [ ] The Mailer rehearsal (`scripts/rehearse_c95_migrations.py run`) exited 0 on the machine that will drive the release.
- [ ] A backup of **each** database exists and **a restore of it has been proven** (section 6.1). Backups: LeadBoost (Railway PostgreSQL), Mailer (Supabase) `[UNKNOWN: check Supabase backups; FACT: SUPABASE]`.
- [ ] `MAILBOX_ENCRYPTION_KEY` is backed up **separately from the database** (a restored Mailer database is useless without it) `[REPO: docs/DEPLOYMENT.md section 6]`.

**Control of deployments (section 4 decisions D1-D3)**
- [ ] Vercel (`lead-boost-saas`): automatic production deployment on merge is **off** or held for the window `[UNKNOWN: check Vercel; FACT: VERCEL]`.
- [ ] Railway (LeadBoost backend and Mailer worker): auto-deploy **off** or held `[UNKNOWN: check Railway; FACT: RAILWAY]`.
- [ ] Render (Mailer API): auto-deploy **off**, and it is known which repository it builds from `[UNKNOWN: check Render; FACT: RENDER]`.
- [ ] You know how the new Mailer code will reach Render (D1) and have not yet re-created `RENDER_MIRROR_TOKEN` (re-creating it makes the next Mailer merge deploy).
- [ ] Exactly **one** Mailer worker will run; the redundant Railway `web` service is stopped or its removal is confirmed `[UNKNOWN: check Railway; FACT: RAILWAY]`.

**Mailer database (you, at deploy time; read-only until the wrapper)**
- [ ] `scripts/migrate_release.sh --status` against the real Mailer database shows which of 010, 011, 012, 013 are applied. It was only ever run for 11 steps on the hosted database, so 010-013 are expected to be **all pending** `[REPO: docs/C13_DEPLOYMENT_RUNBOOK.md section 0, "11 steps"]`.
- [ ] The legacy-reply count query (appendix A) was run and the gate (`scripts/migrate_preflight.py gate`) exits 0.
- [ ] The migration windows are planned for low traffic: 010 builds an index on `messages` under a `SHARE` lock and 012 builds a unique index and backfills `messages` `[REPO: migrations/README.md "Canonical order"]`.

**Sending safety**
- [ ] `LIVE_SENDING_ENABLED=false` and `AUTO_REPLY_ENABLED=false` on every Mailer service until section 7 says otherwise.
- [ ] SMTP reachability from the worker is proven (Railway Limited Trial may restrict outbound SMTP) `[UNKNOWN: check Railway; FACT: RAILWAY]`.
- [ ] Only your own two mailboxes will ever receive mail during the smoke test.

**Rollback**
- [ ] You have read section 8 and know that rolling code back does **not** require a downgrade, and that downgrading 013 destroys approval evidence.

---

## 2. What is being released

| Part | What changes | Source |
|---|---|---|
| Mailer database | Migrations **010** (worker runtime status: 2 tables + a partial index), **011** (unmatched inbound table), **012** (`messages.reply_reference` + unique index + backfill), **013** (`external_dispatches`: `content_approval_required BOOLEAN NOT NULL DEFAULT FALSE`, `approved_content_sha256`, `approved_at`). Additive. | `[REPO: migrations/README.md]` |
| Mailer code | Draft / approve / cancel flow, the exact-content digest, operator endpoints, hardening from PR #30 | `[REPO]` |
| LeadBoost database | Alembic revisions after the baseline: `61258798a87a` -> `1e7ab6ba0976` -> `baee12037c73` -> `c1a7e0d2b9f4` -> `d4b8f2a6c113` -> **`e5c9a3b7d214`** (head). Two create tables, one more creates two tables, three add columns. | `[REPO: backend/alembic/versions/]`, `[REHEARSED]` |
| LeadBoost backend + frontend | Reply composer UI, draft read-back, approve with the reviewed digest | `[REPO]` |

The ORM of **both** services selects the new columns on every query. That is why the database always changes **before**
the code that needs it (section 3, trap 1).

## 3. Findings that shape the order (read these first)

1. **New code started before its migration fails late, not early.** `[REHEARSED]` With the Mailer's default
   `SCHEMA_INIT_MODE=create`, new code starts against the pre-013 schema, `init_db()` adds only missing *tables*, and the
   first dispatch query then fails with `column external_dispatches.content_approval_required does not exist`. With
   `SCHEMA_INIT_MODE=verify` it refuses to start and names the problem `[REPO: mailer_agent/db.py]`.
2. **LeadBoost has the same trap and it can corrupt the adoption.** `[REPO: backend/main.py lifespan]` startup calls
   `init_db()` -> `Base.metadata.create_all`, which creates missing tables and never alters existing ones. `[REHEARSED]` Starting the new app on a
   baseline-state database creates `email_accounts`, `organization_outreach_policies`, `organization_qualification_settings`
   and `outreach_actions` but not `organizations.industry`, `organizations.icp_description`, `users.job_title`,
   `users.signature`. The database is then neither the baseline nor head, `verify_baseline_schema.py` exits 1,
   and `alembic upgrade head` fails with `DuplicateTable`. The app still answers `/live` and `/ready` with 200
   (`/ready` is only `SELECT 1` `[REPO: backend/main.py]`), so **"the app started" proves nothing about the schema**.
   A platform that auto-deploys LeadBoost before you run Alembic produces exactly this state.
3. **The documented Alembic adoption path does not fit the current head.** `[REPO]` `backend/docs/DEPLOYMENT.md`
   says: verify, `alembic stamp eaa40e596fcc` (the baseline), then `alembic upgrade head` ("a no-op"). But
   `scripts/verify_baseline_schema.py` compares the database to the **head** models and its own success message says
   "stamp head", and the repository's test stamps `head`. `[REHEARSED]` On a database that equals the baseline exactly, verify exits 1
   (the documented rule says "do not stamp"). On a database that already equals head, verify exits 0, stamping the baseline
   as documented then fails at the first revision with `DuplicateTable`, while `stamp head` succeeds. Which case your dump is
   is unknown until the probe runs (D4).
4. **The Mailer release is four migrations, not one**, and the hosted database has only had 11 steps applied.
5. **There is no maintenance mode in LeadBoost.** `[REPO]` The only pause is the per-organisation `is_paused` policy,
   and it makes `dispatch_action` mark actions `DISPATCH_FAILED` rather than hold them. The README's step 1 ("503 at the
   edge") has no counterpart you control on Railway unless you add one (D3).
6. **`LIVE_SENDING_ENABLED=false` is not a pause.** `[REPO: docs/DEPLOYMENT.md section 6]` Queued work is failed as
   `live_sending_disabled`. The safe pause is stopping the **worker** service; SIGTERM drains up to
   `EXTERNAL_DISPATCH_SHUTDOWN_DRAIN_SECONDS` (25 s), turns anything still `SENDING` into `UNKNOWN`, leaves the rest
   `QUEUED`. An in-flight send interrupted by a stop becomes `UNKNOWN` and needs reconciliation by `Message-ID`; it is never re-sent.

## 4. Decisions that are yours (this runbook does not choose)

**D1 - How new Mailer code reaches Render.** `[REPO: .github/workflows/sync-render-repo.yml]` The mirror workflow copies CI-verified
`main` to `anonymous-martin/LeadBoost-mail-agent` **only if** the secret `RENDER_MIRROR_TOKEN` exists, otherwise it stops with `STOP: repository secret RENDER_MIRROR_TOKEN is missing.`
Its runs: #7 (Oct 27) and #8 (Oct 30) succeeded; #9 (Nov 19) and #10 (Nov 23) failed `[owner-reported; reason line: FACT: RENDER]`.
So the Render-connected repository is probably **behind** (last mirrored before C9.5) and merging does not deploy.

| Option | Consequence |
|---|---|
| a. Re-create the mirror secret | Restores the automatic path, **and** the very next Mailer merge deploys to Render, including docs-only merges. Re-create it only inside the window, after Mailer migrations. |
| b. Point Render at the canonical repository | Removes the mirror. Render then deploys on that repository's `main` if Auto-Deploy is on; set it to manual for the window. |
| c. Deploy manually from the dashboard | No automation to trip over; you pick the commit. The Render-connected repository must still contain that commit (so a/b is needed at least once), or Render must be switched to a branch that does. |

**D2 - Preventing an auto-deploy from starting LeadBoost before Alembic.** Per finding 2 this must be impossible during
the window. Options: set Railway to manual/disable auto-deploy; hold the merge of the LeadBoost PR until after Alembic; keep Railway on the old
image until the schema is migrated. `[UNKNOWN: check Railway; FACT: RAILWAY]` (settings are not in the repositories).
Also for **Vercel** `[UNKNOWN: check Vercel; FACT: VERCEL]`: there is no `vercel.json`; a merge to the production
branch deploys the frontend by default, and a frontend that calls `/reply-draft` before the backend has it gets 404s.
Merge order matters for this reason alone: backend live and verified, then frontend.

**D3 - Pausing LeadBoost reply traffic (README step 1).** No built-in mechanism (finding 5). First find out whether
it matters: run the **read-only count query** in appendix A on the Mailer database. If there are no legacy-shaped managed
replies in `queued`, `generating` or `sending`, the gate passes trivially and no pause is needed. (Before C9.5 there was no reply UI,
so only a direct caller of the C9.4 reply API could have created one `[REPO: backend/docs/C9_5_MANAGED_REPLY_CONTENT_APPROVAL.md]`;
and with the hosted worker on `LIVE_SENDING_ENABLED=false` any claimed one was already failed `live_sending_disabled`
`[REPO: docs/DEPLOYMENT.md]` - both are inferences until the count query is run.)
If rows exist, the smallest safe choices are: (i) schedule the window when nobody uses the product and stop the LeadBoost
backend service for the drain, or (ii) add an edge/proxy rule. Do not use the per-organisation `is_paused` policy as a pause: it fails dispatches.

**D4 - LeadBoost Alembic adoption.** Run `rollout_rehearsal.py probe` on the restored dump copy (section 6.2). Its verdict
decides:

| Probe verdict | verify exit | What it means | Path |
|---|---|---|---|
| `at-head` | 0 | schema already equals head | `stamp head` (what the verifier and the repo test do); **not** `stamp eaa40e596fcc` |
| `clean-at-revision <R>` where R is the baseline | 1 | exactly the baseline | the documented rule says stop. The correct operation is `stamp eaa40e596fcc` then `upgrade head`, but the documented gate refuses it. **Needs your decision and a reviewed change to the documentation/gate; the rehearsal can only show you what it would do on a copy** (`adopt --continue-after-verify-failure`). |
| `clean-at-revision <R>` for another R | 1 | exactly revision R | same, with `stamp R` |
| `mixed` | 1 | e.g. `create_all` ran after an older schema | no stamp is safe. Restore from the pre-migration backup and decide on a reviewed, hand-written reconciliation. Do not edit the live database. |

Until D4 is decided, **do not run `alembic stamp` against production.**

**D5 - `SCHEMA_INIT_MODE=verify` on the Mailer services.** `[REPO: docs/DEPLOYMENT.md "Switching a service to SCHEMA_INIT_MODE=verify"]`
`[REHEARSED]` it turns finding 1 from a late 500 into a refusal to start. Recommended for the window; staged and reversible as the Mailer doc describes.

**D6 - Who is the operator of record** during the window and who can approve a rollback (named in the evidence record).

## 5. How code reaches each platform (verify on your dashboards)

| Service | Repository / branch it builds from | Auto-deploy | Source |
|---|---|---|---|
| Mailer API (Render) | `[UNKNOWN]` (likely the mirror repo) | `[UNKNOWN]` (`render.yaml` has no `autoDeploy` key, so the platform default applies) | FACTS: RENDER |
| Mailer worker (Railway, `python worker.py`) | `[UNKNOWN]` | `[UNKNOWN]` | FACTS: RAILWAY |
| Mailer `web` on Railway (redundant) | `[UNKNOWN: may still exist]` | | FACTS: RAILWAY |
| LeadBoost backend (Railway) | `[UNKNOWN]` | `[UNKNOWN]` | FACTS: RAILWAY |
| LeadBoost frontend (Vercel `lead-boost-saas`) | `[UNKNOWN]`; no `vercel.json` | `[UNKNOWN]` (bot comments on every PR) | FACTS: VERCEL |

`[REPO: .github/workflows/ci.yml]` LeadBoost CI has no deploy step. `[REPO: render.yaml]` the Blueprint defines a Render worker, but what has run
is a Railway worker; the runbook "worker deployed through the Render Blueprint" is marked not performed.

---

## 6. Pre-flight

### 6.1 Backups, and proof they restore
1. Back up the LeadBoost database and the Mailer database with the provider's tool. Record type, time and retention `[FACT: SUPABASE]`.
2. **Prove** each backup restores: restore it into a throw-away database that is not production, and compare table row counts. The tooling for the
   LeadBoost side is built in (`rollout_rehearsal.py adopt` does it and fails if the counts differ); for Supabase use the provider's restore-to-new-project or a
   `pg_dump -Fc` restored into a local `c95_rehearsal_*` database. A backup you have not restored is not a backup.
3. Back up `MAILBOX_ENCRYPTION_KEY` offline, separately.

### 6.2 Rehearsals on your machine (never against production)
Prerequisite: the throw-away container `c95-pg16` (PostgreSQL 16, `127.0.0.1:56432`). All commands refuse anything else.
- Mailer (synthetic data; ~1 minute): `python scripts/rehearse_c95_migrations.py run` - exit 0 means every expectation held.
- LeadBoost, on the restored production dump: the exact command sequence is in `docs/evidence/ROLLOUT_REHEARSAL_RESULTS.md` (inspect-dump, restore, probe,
  app-start with the job loop off, adopt, wrong-order, cleanup). The dump is read from stdin and is never printed, copied or committed.

### 6.3 Read-only questions only you can answer at deploy time
Run from a machine that can reach the Mailer database, with `DATABASE_URL` set for that shell only, and with the repository on the commit to be deployed:
```
python scripts/migrate_preflight.py status      # which of 010/011/012/013 objects exist (read-only)
python scripts/migrate_preflight.py verify      # what SCHEMA_INIT_MODE=verify would say (read-only)
python scripts/migrate_preflight.py gate        # legacy managed-reply gate (read-only); exit 4 = unresolved rows
scripts/migrate_release.sh --status             # the guarded wrapper's own status (read-only)
```
Never paste the connection string into a chat, an issue or a log.

---

## 7. The order (Mailer first, then LeadBoost)

This maps onto the 7 steps of `migrations/README.md` "Legacy managed replies" and "Release procedure". Do not skip the README steps; they are linked, not copied.

| # | Stage | Action | Verify before continuing | If it fails |
|---|---|---|---|---|
| 0 | Freeze | Decisions D1-D6 made. Auto-deploy off everywhere that matters. Release commits identified (read the real merge SHAs from `origin/main`). | Go/no-go page all yes. | Stop. |
| 1 | Mailer: legacy gate (README steps 1-6) | If appendix A shows legacy rows in blocking states: pause traffic (D3), drain with the OLD worker, abandon or wait, gate -> exit 0. If it shows none, the gate is trivial: still run it. | `gate` exits 0. | Resolve rows per README step 5; never abandon a `sending` row. |
| 2 | Mailer: database | `scripts/migrate_release.sh --check-only`, then the real run with `MIGRATE_CONFIRM_BACKUP=I_HAVE_A_VERIFIED_BACKUP_OF_<database name>` and a lock timeout (`MIGRATE_LOCK_TIMEOUT_MS`). It runs the gate, `init`, then all 15 steps; it is idempotent. | `--status`: all markers present; `verify` exit 0. | The wrapper never runs downgrades, and every step is idempotent (`[REHEARSED]`: re-running the whole wrapper after success is a no-op, exit 0). Fix the cause and re-run; do not edit the database by hand. |
| 3 | Mailer: code | Optionally `SCHEMA_INIT_MODE=verify` first (D5). Deploy the **API**, then stop the old worker (SIGTERM, wait, confirm none left), then start the **one** new worker. `render.yaml`'s `maxShutdownDelaySeconds: 60` exceeds the 25 s drain budget; the equivalent Railway setting `[UNKNOWN]`. | `GET /health` 200. Operator `GET /system/metrics/worker` shows exactly one `fresh` heartbeat (needs migration 010 **and** the PR-4 code; before both it is not meaningful). `GET /system/metrics/dispatch` sane (no `unknown`, no stale `sending`). | Roll the **code** back (section 8). Do not downgrade. |
| 4 | LeadBoost: database | **Before** any new LeadBoost code starts. Per D4. Back up, prove the backup, run the chosen sequence, `alembic check` clean, `alembic current` = `e5c9a3b7d214`. | `alembic check`: "No new upgrade operations detected". | Restore the backup into a new database; never hand-edit. |
| 5 | LeadBoost: backend code | Deploy the new backend (Railway). | `/live` 200, `/ready` 200 (database reachable only), `alembic current` unchanged, and a read of an existing organisation works (schema proof, since `/ready` is not one). | Roll code back (section 8). |
| 6 | LeadBoost: frontend | Release the Vercel production deployment **after** step 5 is verified. | The reply composer renders; open a reply of the dummy organisation. | Promote the previous Vercel deployment. |
| 7 | Lift the pause | If D3 used one. | | |
| 8 | Smoke test | `docs/C9_5_SMOKE_TEST.md`, including the one controlled `LIVE_SENDING_ENABLED=true` acceptance. | The verifier script and your own two mailboxes. | Stop; section 8. |
| 9 | Evidence | Fill `docs/evidence/C9_5_HOSTED_ACCEPTANCE_TEMPLATE.md`. Only then close issue #20. | | |

Worker discipline: exactly one Mailer worker at all times. Two workers would double-claim.

## 8. Rollback

**Principle** `[REPO: docs/DEPLOYMENT.md section 6]`: code rolls back by redeploying the previous revision; the schema does not roll back
automatically and the wrapper never downgrades. Migrations are additive, so old code is *expected* to keep working on the new schema
(extra columns and tables are not selected by it). That expectation is read from the migrations, **not** rehearsed.

| Stage reached | Roll back by | Downgrade needed? | What to know |
|---|---|---|---|
| Mailer database migrated, code not deployed | nothing | **No** | The change is additive (`[REPO: migrations/README.md]`); the new flag defaults to FALSE. Old code running on the new schema was **not** rehearsed (the harness has no old-code build): watch the first minutes after step 2. |
| Mailer code deployed, problem found | redeploy the previous Mailer revision (API, then worker) | **No** | What the old code does with a draft the new code left in `AWAITING_APPROVAL` is `[UNKNOWN: not rehearsed]`; leave such a draft alone and cancel it from the new code later. |
| LeadBoost database migrated, code not deployed | nothing | No | The revisions only add tables and columns (`[REPO: backend/alembic/versions/]`); old code on the new schema was not rehearsed. |
| LeadBoost backend deployed, problem found | redeploy the previous backend | No | Old LeadBoost calling the new Mailer: README step 7 - an older caller gets a held draft that never sends (fails closed). |
| Frontend | promote the previous Vercel deployment | No | |
| Anything needing a schema downgrade | only after reading below | Mailer: `migrations/013_... --downgrade`; LeadBoost: `alembic downgrade -1` | See below. |

**Do not downgrade 013 once any managed reply has been approved.** `[REHEARSED]` The Mailer downgrade refuses (exit 1) while a managed-reply
dispatch depends on the columns, and once it succeeds it **drops `approved_content_sha256` and `approved_at`: the approval evidence is gone**,
and re-applying 013 does not bring it back. Downgrading is a data-loss decision, not a rollback step.
**LeadBoost downgrades** `[REHEARSED]`: `downgrade -1` from head removes only `outreach_actions.reply_draft_requested_at`,
`approved_content_sha256`, `approved_draft_version` and restores the schema exactly (schema diff empty after downgrade + upgrade).
Going further down destroys data: the revision that created `email_accounts`, `organization_outreach_policies`/`outreach_actions`
or `organization_qualification_settings` drops those tables **with their rows**. Never downgrade below the baseline.

**What becomes unreachable after a rollback:** drafts created by the new code can no longer be reviewed in the UI of old code; nothing is deleted. Replies already `sent`
stay sent. `UNKNOWN` dispatches are never retried by anyone: reconcile each against the sender's Sent folder using its `Message-ID`.

**Decision points (write the answer in the evidence record):**
1. After step 2: is it acceptable to leave the Mailer schema migrated while the code is old? (Yes by design.)
2. After step 4: if `alembic check` is not clean, restore the backup; do not repair live.
3. After the first real approval exists: downgrades are off the table; roll **code** only.

---

## Appendix A - read-only SQL (run on the real database by you; never paste results containing addresses)

**A1. Do any legacy managed replies exist, and in which states? (Mailer; works before and after 013; `json` or `jsonb`)** `[REHEARSED: scripts/rehearse_c95_migrations.py LEGACY_COUNT_SQL]`
```sql
SET default_transaction_read_only = on;
SELECT state, (message_id IS NOT NULL) AS has_message, count(*)
  FROM external_dispatches
 WHERE jsonb_typeof(grounding_context::jsonb -> 'reply_parent') = 'object'
 GROUP BY 1, 2 ORDER BY 1, 2;
```
Blocking for the 013 gate: `queued`, `generating`, `sending`. `unknown` is reported but does not block.

**A2. Dispatch state counts (Mailer)**
```sql
SET default_transaction_read_only = on;
SELECT state, count(*) FROM external_dispatches GROUP BY 1 ORDER BY 1;
```

**A3. Alembic position (LeadBoost)**
```sql
SET default_transaction_read_only = on;
SELECT version_num FROM alembic_version;   -- the table is absent before adoption
```

## Appendix B - what could not be verified from the repositories
Everything marked `[UNKNOWN]`; the hosted Mailer's applied-migration set; whether the PR-4 operator endpoints are deployed (the C13 runbook says
`/system/metrics/worker` is "PR 4, not yet deployed or observed anywhere"); the mirror failure reason; each platform's auto-deploy setting;
Railway's SMTP policy; Supabase backup retention; the production dump's schema shape and PostgreSQL version.
