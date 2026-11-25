# C9.5 restore drill

Purpose: prove that a backup of each database can be **restored and used**, with pass/fail criteria and a measured restore time.
A backup that has never been restored is not a backup (`C9_5_ROLLOUT_RUNBOOK.md` section 6.1). Run it before the rollout window and after any
change to how backups are taken. Nothing here touches production: it works on a **restored copy** in the throw-away PostgreSQL container
(`c95-pg16`, `127.0.0.1:56432`); every command refuses any other server or any database not named `c95_rehearsal_*`.

## 1. LeadBoost: restore the dump copy

Commands (the helper functions come from `tools/env_v6.sh`; the dump is read from stdin and never printed, copied or committed):

```
source /mnt/g/DEV/verification/c95-v3-baseline/env2.sh
source "$HOME/c95-v5-pkg/c95-v5/tools/env_v5.sh"
source "$HOME/c95-v5-pkg/env_v5_fix.sh"
source "$HOME/c95-v6a-pkg/c95-v6a/tools/env_v6.sh"
set_rehearsal_env                       # sets the REHEARSAL_* variables from the c95-pg16 settings; prints nothing secret
lbr inspect-dump --dump "$GH_LB/prod-pre-alembic-adoption.dump"
lbr restore --dump "$GH_LB/prod-pre-alembic-adoption.dump" --label drill
DB=$(lbr_last_db)
lbr probe --db "$DB"
lbr app-start --db "$DB"
lbr counts --db "$DB"
```

| # | Criterion | PASS | FAIL / stop |
|---|---|---|---|
| 1 | `inspect-dump` recognises the format and the archive version is readable by the container's `pg_restore` | exit 0 | exit 3 with "unsupported version": the dump was made by a newer PostgreSQL than 16; **stop**, creating another `c95-*` container needs the owner's consent |
| 2 | `restore` completes | `restored database has tables`; 0 or only ignorable error lines (roles/ownership) | any error about a missing table or data |
| 3 | Row counts after restore are plausible against what you know of production (counts only are printed) | yes | a table you know has data shows 0 |
| 4 | `probe` verdict is recorded (`at-head`, `clean-at-revision <R>`, `mixed`) | any, but it **must be written down**: it decides the adoption path (runbook D4) | |
| 5 | `app-start` -> `/live` 200 and `/ready` 200 | yes | no. Note: `/ready` is only `SELECT 1`, it does **not** prove the schema; `/health` is 503 without Redis and is informational |
| 6 | App start created **no** tables and changed **no** counts | `startup created no tables` and `startup changed no row counts` | a created table means `create_all` ran against an old schema: see runbook finding 2 |
| 7 | After adoption (runbook D4 path): `alembic current` = `e5c9a3b7d214`, `alembic check` clean | yes | restore again; do not repair |

The app is started with `JOB_WORKER_ENABLED=false`, no LLM/SMTP/Stripe/Mailer keys and `ENVIRONMENT=development`: without that the in-process job loop would
claim the dump's queued jobs and scrape real websites.

**RTO note.** `restore` prints the restore time. That number is a **laptop measurement** on a ~1 MB dump through Docker Desktop; it says nothing about
the production database's size, the provider's restore tooling or network. Record it, label it as such, and measure again with the provider's real restore before relying on it.

## 2. Mailer: the same drill on synthetic data

There is no Mailer production dump. `python scripts/rehearse_c95_migrations.py run` (Mailer repository) includes, as its last step, a drill on a **synthetic** database:
`pg_dump -Fc` of the migrated database, `pg_restore` into a new `c95_rehearsal_*` database, and the checks below.

| # | Criterion | PASS |
|---|---|---|
| 1 | restored row counts equal the source for every table | `restored row counts equal the source` |
| 2 | every migration marker present (`migrate_release.sh --status`) | all present |
| 3 | the legacy gate passes | exit 0 |
| 4 | the API starts with `SCHEMA_INIT_MODE=verify` against the restored copy | `startup ok` |

**Real Mailer restore (Supabase):** use the provider's restore into a **new** project/branch, never over production. A restored Mailer database is only useful with the
**original `MAILBOX_ENCRYPTION_KEY`**: stored mailbox credentials cannot be decrypted without it (`docs/DEPLOYMENT.md` section 6). Verify with a read-only `GET /health` and
`python scripts/migrate_preflight.py status` against the restored copy, from a service configured with that key and with `LIVE_SENDING_ENABLED=false`.

## 3. Cleanup

`lbr cleanup` (LeadBoost) and `mar cleanup` (Mailer) drop **only** the databases recorded in the harness' own state file. They never touch another database.
