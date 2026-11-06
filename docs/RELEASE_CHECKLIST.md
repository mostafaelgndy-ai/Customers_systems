# Release Checklist (TEST-14)

Every release (staging or prod) goes through this checklist. Do not skip
items — each one maps to a tested or documented property of the system.

## 1. Configuration

-   [ ] `ENVIRONMENT` set to `staging` or `prod` (never `dev`).
-   [ ] `SECRET_KEY` explicitly set, **not** the development default, and
        at least 32 characters. The backend refuses to start in prod with
        the development default (enforced by
        `Settings._reject_insecure_prod_settings`, TEST-17).
-   [ ] `DATABASE_URL` points at the real PostgreSQL instance (SQLite is
        rejected in prod by the same validator).
-   [ ] `REDIS_URL` points at the shared cache/broker.
-   [ ] `INLINE_JOBS=false` — jobs must run on Celery workers, not in the
        API process.
-   [ ] `CORS_ORIGINS` lists the exact browser origins that will call the
        API (e.g. `https://app.example.com`). Use `*` never.
-   [ ] `NEXT_PUBLIC_API_URL` is the **public** URL/API reachable from
        users' browsers; it is baked into the frontend at build time.
-   [ ] Data paths (`UPLOAD_DIR`, `MODELS_DIR`, `EXPORTS_DIR`) point at
        persistent volumes: uploads, exports, and model artifacts must
        survive container restarts (the compose stack uses
        `uploads:`/`exports:` named volumes shared between backend and
        worker; `models` currently lives on the backend container — see
        "Known gaps").
-   [ ] Rate limiting considered: `RATE_LIMIT_ENABLED=true` for
        any deployment with unknown users (limits documented in
        `.env.example`, SEC-04).

## 2. Database migrations

-   [ ] Run `alembic upgrade head` against the target database **before**
        starting the new backend (the compose backend command does this).
-   [ ] Confirm `alembic current` matches the newest migration.
-   [ ] Take a fresh backup before migrating (§ 3).

## 3. Backups (TEST-15, docs/BACKUP_RESTORE.md)

-   [ ] Backup procedure verified on this environment at least once
        (documented in `docs/BACKUP_RESTORE.md`).
-   [ ] Fresh `pg_dump` taken before the release; restore path tested
        with the same procedure.
-   [ ] Volume contents (uploads/exports/models) included in the backup
        plan (they are **not** in `pg_dump`).

## 4. Automated gates (must be green on the release commit)

-   [ ] CI succeeds on the release SHA: ruff, ruff format, mypy,
        backend pytest suite (>= 143 tests), compose config validation,
        and the e2e UI-flow replay job (TEST-12).
-   [ ] Load test executed at least once on a build equal or later than
        the release commit (docs/LOAD_TEST.md, TEST-13).

## 5. Security review (TEST-18, docs/SECURITY_REVIEW.md)

-   [ ] Security review checklist signed off for the release
        (auth, tenant isolation, path handling, audit events, CORS,
        secrets handling).
-   [ ] Audit event log rotation/retention plan agreed (SEC-06).
-   [ ] `docker compose config` validated (CI does this).

## 6. Smoke test after deployment

-   [ ] `GET /api/v1/health/readiness` → `{"status":"ok",...}` (database
        and redis both `ok`).
-   [ ] Register a test user, upload a small customer + orders CSV pair,
        create a version, confirm validation runs to `valid`.
-   [ ] Launch a churn training run; confirm the Celery worker executes
        it (`docker logs <worker>` shows the task) and the run reaches
        `succeeded`; score and browse a customer detail page.
-   [ ] Export download returns a CSV whose row count equals the score
        population.
-   [ ] The frontend dashboard loads with no console errors; CORS
        preflight returns 200 on `OPTIONS` from the deployed origin.

## 7. Rollback plan

-   [ ] Previous backend image tag available (`docker compose images`).
-   [ ] Database restore procedure (`docs/BACKUP_RESTORE.md § restore`)
        rehearsed with the newest dump.
-   [ ] Frontend can be redeployed independently (image is per-commit).

## Known gaps (documented, not release-blocking for the pilot)

-   `models` directory is not currently a shared named volume: model
    artifacts are written on the container that runs the job and read by
    the API from the default `data/models` path inside each container.
    Acceptable while jobs run with `concurrency=2` on one worker and the
    API only reads via the DB-tracked artifact path on the same host;
    a shared volume is recommended before any multi-node deployment.
-   Dataset deletion is role-gated (owner/admin) and cascades all derived
    artifacts; audit rows are retained by design (SEC-06).
