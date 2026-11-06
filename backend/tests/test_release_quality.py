"""Phase 8 release-quality tests (TEST-03/04/05/06/10/17/18).

Complements the MIT-domain suites: scoring/period leakage, reproducibility
across API runs, degenerate populations, unknown job types, and the
security-relevant configuration guardrails (prod secrets, CORS).
"""

from __future__ import annotations

import uuid

import pandas as pd
import pytest

from app.core.config import DEV_SECRET, Settings
from app.domains.churn.trainer import TrainingConfig, train_churn_models
from app.domains.features.pipeline import FEATURE_COLUMNS, FeatureConfig, compute_features
from app.domains.jobs.models import JOB_FAILED, Job
from app.domains.jobs.queue import run_job
from tests.test_training import _csv_dataset  # synthetic 18-month dataset (trains reliably)

# --- TEST-03: no temporal leakage in generated features ------------------------


def test_cutoff_features_are_invariant_to_future_orders() -> None:
    """Features computed at cutoff C must be identical whether or not the
    frame also contains orders AFTER C — future data can arrive in a later
    file/version and must never change a past snapshot (leakage safety)."""
    customers = pd.DataFrame(
        {
            "customer_id": ["C1", "C2"],
            "created_at": ["2024-01-05", "2024-01-10"],
        }
    )
    orders = pd.DataFrame(
        {
            "customer_id": ["C1", "C2", "C1", "C2", "C1", "C2"],
            "order_id": ["O1", "O2", "O3", "O4", "O5", "O6"],
            "order_date": ["2024-02-01", "2024-02-05", "2024-03-01", "2024-03-10", "2024-04-01", "2024-04-20"],
            "order_status": "completed",
            "order_amount": "100",
        }
    )
    config = FeatureConfig(cutoff_date="2024-03-15", observation_days=180)

    with_future = compute_features(customers, orders, config)  # contains April orders
    without_future = compute_features(customers, orders[:-2].copy(), config)  # April removed

    keyed_future = {row["customer_id"]: row for row in with_future}
    keyed_without = {row["customer_id"]: row for row in without_future}
    assert keyed_future.keys() == keyed_without.keys()
    for customer_id in keyed_without:
        for column in FEATURE_COLUMNS:
            assert keyed_future[customer_id][column] == keyed_without[customer_id][column], (
                f"{customer_id}.{column} changed when future (post-cutoff) orders existed — leakage"
            )

    # Sanity: the two frames genuinely differed in inputs.
    assert len(orders) - 2 == 4


# --- TEST-04: reproducibility across full API runs ------------------------------


def test_training_is_reproducible_across_runs(client, org_a_owner) -> None:
    """Two identical API training runs (same data, seed, params) on the same
    version produce the same cutoffs and the same evaluation metrics."""
    headers = org_a_owner["headers"]
    dataset_id, version_id = _csv_dataset(client, headers)

    payload = {"dataset_version_id": version_id, "min_period_customers": 10, "random_seed": 42}
    run_ids = []
    metrics = []
    for _ in range(2):
        response = client.post("/api/v1/churn/model-runs", json=payload, headers=headers)
        assert response.status_code == 202, response.text
        run_ids.append(response.json()["run"]["id"])
        detail = client.get(f"/api/v1/churn/model-runs/{run_ids[-1]}", headers=headers)
        assert detail.json()["run"]["status"] == "succeeded", detail.text
        metrics.append(detail.json()["run"]["metrics"])

    assert metrics[0]["split"] == metrics[1]["split"]  # determinism (TEST-04)
    for model in ("logistic_regression", "random_forest"):
        assert metrics[0]["validation"][model]["pr_auc"] == metrics[1]["validation"][model]["pr_auc"]
        assert metrics[0]["validation"][model]["roc_auc"] == metrics[1]["validation"][model]["roc_auc"]
    assert metrics[0]["test"] == metrics[1]["test"]


# --- TEST-05/06: degenerate and too-small populations ---------------------------


def _all_active_frames() -> tuple[pd.DataFrame, pd.DataFrame]:
    """18 months where every customer keeps ordering → zero churners anywhere."""
    customers, orders = [], []
    for i in range(40):
        cid = f"CA{i:03d}"
        customers.append({"customer_id": cid, "created_at": "2024-01-01"})
        for month in range(18):
            orders.append(
                {
                    "customer_id": cid,
                    "order_id": f"OA{i:03d}-{month:02d}",
                    "order_date": (pd.Timestamp("2024-01-01") + pd.Timedelta(days=month * 30 + 5)).date().isoformat(),
                    "order_status": "completed",
                    "order_amount": "50",
                }
            )
    return pd.DataFrame(customers), pd.DataFrame(orders)


def test_single_class_population_fails_with_actionable_error() -> None:
    """A population with no churners must fail training with a clear message,
    not crash or 'succeed' with garbage probabilities (TEST-05)."""
    customers, orders = _all_active_frames()
    with pytest.raises(ValueError) as excinfo:
        train_churn_models(customers, orders, TrainingConfig(label_horizon_days=90))
    message = str(excinfo.value)
    assert "label classes" in message or "churned" in message
    assert "observation window" in message or "history" in message  # actionable hint


def test_span_too_short_for_horizon_explains_dates() -> None:
    """TEST-06: a few weeks of data cannot be split into three horizon
    periods; the error names the first/last dates so the user can fix it."""
    from app.domains.churn.trainer import derive_cutoffs

    tiny = pd.DataFrame(
        {
            "customer_id": ["T1"] * 3,
            "order_id": ["T-O1", "T-O2", "T-O3"],
            "order_date": ["2025-01-10", "2025-02-10", "2025-03-10"],
            "order_status": "completed",
            "order_amount": "50",
        }
    )
    with pytest.raises(ValueError, match="too short"):
        derive_cutoffs(tiny, TrainingConfig(label_horizon_days=90))


# --- TEST-10: unknown job types fail cleanly ------------------------------------


def test_unknown_job_type_fails_not_stuck(app_db) -> None:
    """A job row whose job_type has no registered handler must end `failed`
    with an explanatory error, never stay `running` (background safety)."""
    _engine, factory = app_db
    session = factory()
    job = Job(
        id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        job_type="not_a_real_type",
        created_by_user_id=uuid.uuid4(),
    )
    session.add(job)
    session.commit()

    from app.domains.identity.models import Membership  # noqa: F401  (models imported already in conftest)

    run_job(session, job)
    session.commit()
    assert job.status == JOB_FAILED
    assert "No handler" in (job.error or {}).get("message", "") or "No handler" in str(job.error)
    session.close()


# --- TEST-17: production configuration guards ------------------------------------


def test_prod_rejects_default_secret() -> None:
    with pytest.raises(ValueError, match="SECRET_KEY"):
        Settings(environment="prod", secret_key=DEV_SECRET, database_url="sqlite:///prod.db")


def test_prod_rejects_short_secret() -> None:
    with pytest.raises(ValueError, match="SECRET_KEY"):
        Settings(environment="prod", secret_key="too-short", database_url="postgresql://prod/db")


def test_prod_rejects_sqlite_database() -> None:
    with pytest.raises(ValueError, match="DATABASE_URL"):
        Settings(environment="prod", secret_key="x" * 40, database_url="sqlite:///prod.db")


def test_prod_accepts_secure_settings() -> None:
    settings = Settings(
        environment="prod",
        secret_key="x" * 40,
        database_url="postgresql+psycopg://prod@db/prod",
    )
    assert settings.is_prod


# --- TEST-18: CORS for the dashboard origin --------------------------------------


def test_cors_allowlisted_origin_and_preflight(client, org_a_owner) -> None:
    # Simple cross-origin GET carries the allow-origin header.
    response = client.get(
        "/api/v1/datasets",
        headers={**org_a_owner["headers"], "Origin": "http://localhost:3000"},
    )
    assert response.headers.get("access-control-allow-origin") == "http://localhost:3000"

    # Preflight for a CORS-safelisted-forbidden header (Authorization).
    preflight = client.options(
        "/api/v1/datasets",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "Authorization, X-Organization-ID",
        },
    )
    assert preflight.status_code in (200, 204)
    assert preflight.headers.get("access-control-allow-origin") == "http://localhost:3000"
    assert "authorization" in preflight.headers.get("access-control-allow-headers", "").lower()


def test_cors_rejects_unknown_origin(client) -> None:
    response = client.get(
        "/api/v1/datasets",
        headers={"Origin": "http://evil.example.com"},
    )
    assert "access-control-allow-origin" not in response.headers or (
        response.headers.get("access-control-allow-origin") != "http://evil.example.com"
    )
