# Backup and Restore (TEST-15)

Verified live on this stack on 2026-11-06: full `pg_dump` → fresh
restore → per-table row-count match on every application table
(`users, organizations, memberships, audit_events, churn_scores,
segment_assignments, celery_taskmeta`, … — all source == restored).

## Backup (custom format)

```bash
# inside the running db container: compact, compressed, restorable
docker exec customeriq-db-1 pg_dump -U customeriq -Fc -f /tmp/backup.dump customeriq
docker cp customeriq-db-1:/tmp/backup.dump ./backup-$(date +%F).dump
```

The custom format (`-Fc`) is ~100 KB for the current development data
(15 users, 313 churn scores, 49 audit rows) and preserves
 relevance/ownership semantics for restore elsewhere.

CAUTION: do not pipe binary dumps through PowerShell (`>` rewires text
encoding and corrupts the archive). Use `docker cp`, or run on a POSIX
shell.

## Restore (into any Postgres ≥ same major)

```bash
docker exec <db-container> psql -U customeriq -d postgres -c "CREATE DATABASE restored;"
docker cp ./backup.dump <db-container>:/tmp/restore_in.dump
docker exec <db-container> pg_restore -U customeriq -d restored --no-owner /tmp/restore_in.dump
```

`pg_restore` exits 0 on success; worst case partial failures are logged
per-object (run with `--clean --if-exists` to refresh an existing DB).

## Verify after restore

Always diff **exact counts** (pg_stat estimates are stale after
upsert/delete churn — this was observed during the verification run):

```sql
SELECT count(*) FROM users;             -- and each table below
```

Tables to compare source vs restored: `users`, `organizations`,
`memberships`, `datasets`, `dataset_versions`, `dataset_files`,
`dataset_version_files`, `jobs`, `audit_events`, `churn_model_runs`,
`churn_scores`, `segmentation_runs`, `segment_assignments`.

Roll-forward check: the restored DB's `alembic_version` must equal the
schema head the API expects; `alembic upgrade head` is a no-op on it.

## Schedule (production, OPS-03)

| Item | Policy |
| --- | --- |
| Full dump (`-Fc`) | Nightly via cron/systemd-timer on the host |
| Retention | 14 daily + 8 weekly dumps, then delete |
| Off-host copy | Rsync to separate volume / object storage (see DEPLOYMENT.md) |
| Restore drill | Quarterly: restore into a fresh DB + row-count diff (this page) |
| RPO | ≤ 24 h (nightly), PITR via WAL-g available post-MVP |
| RTO | < 15 min (single container restore + `alembic upgrade head`) |

Automation script: `scripts/backup.sh` (see DEPLOYMENT.md §Backups).
