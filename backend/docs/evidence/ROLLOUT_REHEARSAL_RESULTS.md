# C9.5 rollout rehearsal: commands and results

Fill this in from the terminal output on **your** machine (paste outputs; counts, names of tables, revision ids, exit codes and timings only: **never** row contents, addresses, the dump itself or any connection string).
Rehearsals run only against the throw-away container `c95-pg16` (`127.0.0.1:56432`) and only create databases named `c95_rehearsal_*`.

## 0. Environment (every new terminal tab)
```
source /mnt/g/DEV/verification/c95-v3-baseline/env2.sh
source "$HOME/c95-v5-pkg/c95-v5/tools/env_v5.sh"
source "$HOME/c95-v5-pkg/env_v5_fix.sh"
source "$HOME/c95-v6a-pkg/c95-v6a/tools/env_v6.sh"
v6_docker_ready                  # expects: OK, and c95-pg16 listed as running on 127.0.0.1:56432. If not, STOP and tell the author.
set_rehearsal_env
```
| Field | Value |
|---|---|
| Date, machine, PostgreSQL major in `c95-pg16` | |
| Mailer worktree HEAD / LeadBoost worktree HEAD (`audit_state_v6`) | |

## 1. Mailer rehearsal on synthetic data (about 1-2 minutes)
```
mar run 2>&1 | tee "$LOGS/v6a_mailer_rehearsal.log"; echo "exit=${PIPESTATUS[0]}"
```
Expected: `exit=0`, the last lines `expectations: 63, failed: 0` (the count may change if the script changes; **failed must be 0**). Stop condition: any `FAIL` line: paste the whole log.
| Field | Value |
|---|---|
| exit code / expectations / failed | |
| Wrapper time on the synthetic volume (S7 line) | |
| Restore drill times (S11 line) | |
| Anything surprising | |

## 2. LeadBoost: the dump copy (read-only first)
```
DUMP="$GH_LB/prod-pre-alembic-adoption.dump"
lbr inspect-dump --dump "$DUMP"                      # expected: kind: custom (or plain), exit 0. exit 3 = STOP, paste the output
lbr restore --dump "$DUMP" --label src 2>&1 | tee "$LOGS/v6a_lb_restore.log"
SRC=$(lbr_last_db); echo "$SRC"
lbr probe --db "$SRC" 2>&1 | tee "$LOGS/v6a_lb_probe.log"
```
| Field | Value |
|---|---|
| Dump kind / archive version / "Dumped from database version" | |
| Restore: error lines, time | |
| Row counts after restore (counts only) | |
| **Probe verdict** (`at-head` / `clean-at-revision <R>` / `mixed`) | |
| `verify_baseline_schema.py` exit code and problem count | |
| `alembic_version` table present? | |

## 3. LeadBoost: the wrong order (two copies; the source is not modified)
```
lbr wrong-order --source "$SRC" 2>&1 | tee "$LOGS/v6a_lb_wrong_order.log"
```
| Field | Value |
|---|---|
| Unstamped variant: tables `create_all` created / verdict afterwards / `alembic upgrade head` exit | |
| Stamped-baseline variant: same three | |
| Did the app answer `/live` and `/ready` 200 despite the broken schema? | |

## 4. LeadBoost: the documented adoption (on a fresh restore each time)
```
lbr restore --dump "$DUMP" --label adoptdoc | grep RESTORED; D1=$(lbr_last_db)
lbr adopt --db "$D1" 2>&1 | tee "$LOGS/v6a_lb_adopt_documented.log"; echo "exit=${PIPESTATUS[0]}"
```
Expected: if verify exits 1 the command stops with exit 1 and changes nothing (that is the documented rule). Stop condition: never run `alembic stamp` yourself on anything but a `c95_rehearsal_*` copy.

Then, **only on copies**, whichever applies to the probe verdict in section 2:
```
# verdict at-head:
lbr restore --dump "$DUMP" --label adopthead | grep RESTORED; D2=$(lbr_last_db)
lbr adopt --db "$D2" --stamp head --full-downgrade-chain 2>&1 | tee "$LOGS/v6a_lb_adopt_head.log"
# verdict clean-at-revision (baseline): a rehearsal device, NOT the documented procedure
lbr restore --dump "$DUMP" --label adoptbase | grep RESTORED; D3=$(lbr_last_db)
lbr adopt --db "$D3" --continue-after-verify-failure --full-downgrade-chain 2>&1 | tee "$LOGS/v6a_lb_adopt_baseline.log"
# verdict mixed: do NOT run either. Report the probe output; no adoption path exists in the repository.
```
| Field | Value |
|---|---|
| Documented sequence: verify exit / outcome | |
| Chosen copy sequence: revisions applied, time | |
| `alembic check` after upgrade | |
| Schema after downgrade -1 and upgrade head identical? | |
| Rows destroyed by the downgrade chain (per table) | |
| Backup proof: counts equal? | |

## 5. LeadBoost: app start after adoption (the job loop is off; no keys)
```
lbr app-start --db "$D2"     # or $D3, whichever reached head
```
| Field | Value |
|---|---|
| `/live` / `/ready` / `/health` | |
| Tables created / count changes (expect none) | |

## 6. Cleanup (drops only what the harnesses recorded)
```
lbr cleanup; mar cleanup
```

## 7. What this does and does not prove
Proves: how the scripts and the migrations behave on a restored copy of **the dump you have**, on PostgreSQL 16 in a container on your laptop.
Does not prove: behaviour on Railway/Supabase hardware, lock times on the real `messages` table, old-code-on-new-schema, or anything about hosted services.
