# CustomerIQ

B2B customer intelligence for e-commerce: churn prediction and behavior-based
segmentation on historical customer/order data.

> **Status:** Phase 1–2 (repository, standards, data contract, ingestion &
> validation). See `docs/TASKS.md` for the full backlog and `docs/PRD.md` for
> the product contract.

## Repository layout

```
docs/            PRD, task backlog, data contract, API conventions
backend/         FastAPI service + Celery workers (Python 3.12+)
  app/
    core/        config, security, logging, errors, pagination
    db/          engine, session, declarative base
    domains/
      identity/  users, organizations, memberships, auth endpoints
      datasets/  upload, mapping, validation engine, dataset versions
      jobs/      background job lifecycle + dispatch (inline/Celery)
    workers/     Celery application & task entrypoints
  templates/     canonical CSV templates (downloadable)
  tests/         pytest suite (fixtures CSVs, engine units, API, isolation)
frontend/        Next.js dashboard (UI phase comes later; shell included)
ml/              Phase 3–5 feature/churn/segmentation pipelines (placeholder)
```

## Quickstart (local Python, no Docker)

```bash
cd backend
python -m venv ../.venv
../.venv/Scripts/pip install -e ".[dev]"       # Windows PowerShell
../.venv/Scripts/pytest                         # tests use SQLite + inline jobs
../.venv/Scripts/uvicorn app.main:app --port 8000
```

Environment: copy `.env.example` to `.env` (root) — settings are read from
environment variables; SQLite with `INLINE_JOBS=true` is the no-services dev
mode. PostgreSQL/Redis/Celery are used via Docker below.

## Quickstart (Docker, full stack)

```bash
docker compose up --build
```

- API: http://localhost:8000 (docs at `/docs`)
- Frontend: http://localhost:3000
- PostgreSQL: localhost:5432, Redis: 6379
- The backend container runs `alembic upgrade head` before starting.

## Engineering standards

- **Formatting/linting:** `ruff format` + `ruff check` (config in
  `backend/pyproject.toml`); pre-commit hooks in `.pre-commit-config.yaml`.
- **Type checking:** `mypy backend/app`.
- **Tests:** `pytest backend/tests`.
- **Error envelope & pagination:** `docs/api-conventions.md`.
- **Data contract (canonical CSV schemas, policies):** `docs/data-contract.md`.
- **Correlation:** every request/job carries an `X-Request-ID` in logs and
  error responses. PII never enters logs (`app/core/logging.py`).
- **Secrets:** real credentials live only in `.env` (git-ignored);
  `.env.example` documents every variable.

## Data ingestion workflow (implemented)

1. `POST /api/v1/auth/register` → creates user + organization (owner role).
2. `POST /api/v1/datasets` → create a container.
3. `POST /api/v1/datasets/{id}/files?kind=customers|orders` → upload CSV;
   response includes detected headers + suggested column mapping.
4. `PATCH /api/v1/datasets/{id}/files/{fid}` → confirm mapping.
5. `POST /api/v1/datasets/{id}/versions` → bind both files into an immutable
   version and start the validation job (inline in dev, Celery with workers).
6. `GET /api/v1/datasets/{id}/versions/{vid}` → status + full validation
   report (issues with row numbers, per-code counts, coverage metrics).
7. `POST .../versions/{vid}/cancel` / `retry` for job lifecycle control.

Dataset versions are immutable; failures never overwrite earlier valid
versions; field rules come from `docs/data-contract.md`.
