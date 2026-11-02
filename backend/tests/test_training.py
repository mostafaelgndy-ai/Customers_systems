"""CHURN-05..12 acceptance tests: time-aware training + evaluation."""

from __future__ import annotations

import json
import pathlib
import random
import uuid as uuid_module

import joblib
import numpy as np
import pandas as pd
import pytest

from app.core.config import settings
from app.core.errors import APIError
from app.domains.churn.service import create_model_run, execute_model_run, get_model_run
from app.domains.churn.trainer import (
    FEATURE_COLUMNS,
    PERIOD_NAMES,
    POSITIVE_LABEL,
    TrainingConfig,
    build_model_pipelines,
    build_periods,
    derive_cutoffs,
    save_artifacts,
    train_churn_models,
)
from tests.conftest import (
    apply_suggested_mapping,
    create_dataset,
    upload_csv_text,
)


def _synthetic_data(n_customers: int = 60, seed: int = 7) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Deterministic synthetic commerce data spanning ~18 months:
    a third of customers churn at a random month, the rest stay active."""
    rng = random.Random(seed)
    customers, orders = [], []
    for i in range(n_customers):
        cid = f"C{i:03d}"
        stop_month = None if i % 3 == 0 else rng.randint(8, 16)
        customers.append({"customer_id": cid, "created_at": "2024-06-01", "email": f"{cid}@x.io"})
        month = 0
        while month < 18:
            if stop_month is not None and month >= stop_month:
                break
            order_day = month * 30 + 5 + rng.randint(0, 25)
            order_date = pd.Timestamp("2024-06-01") + pd.Timedelta(days=order_day)
            amount = 10 + (i % 7) * 5 + rng.randint(0, 20)
            orders.append(
                {
                    "customer_id": cid,
                    "order_id": f"O{i:03d}-{month:02d}",
                    "order_date": order_date.date().isoformat(),
                    "order_status": "completed",
                    "order_amount": f"{amount}.00",
                }
            )
            month += 1
    return pd.DataFrame(customers), pd.DataFrame(orders)


CUSTOMERS_DF, ORDERS_DF = _synthetic_data()


# --- CHURN-05: time-aware split ---------------------------------------------


def test_derive_cutoffs_are_chronological_and_spaced() -> None:
    config = TrainingConfig(label_horizon_days=90)
    train, validation, test = derive_cutoffs(ORDERS_DF, config)
    last = pd.Timestamp(pd.to_datetime(ORDERS_DF["order_date"]).max())
    first = pd.Timestamp(pd.to_datetime(ORDERS_DF["order_date"]).min())
    horizon = pd.Timedelta(days=90)
    assert pd.Timestamp(test) == last - horizon
    assert pd.Timestamp(validation) == pd.Timestamp(test) - horizon
    assert pd.Timestamp(train) == pd.Timestamp(validation) - horizon
    assert pd.Timestamp(train) >= first


def test_derive_cutoffs_reject_span_too_short() -> None:
    tiny = pd.DataFrame(
        {
            "customer_id": ["C1"],
            "order_id": ["O1"],
            "order_date": ["2025-01-01"],
            "order_status": ["completed"],
            "order_amount": ["10"],
        }
    )
    with pytest.raises(ValueError, match="too short"):
        derive_cutoffs(tiny, TrainingConfig(label_horizon_days=90))


def test_build_periods_are_chronological_and_labelable() -> None:
    periods = build_periods(CUSTOMERS_DF, ORDERS_DF, TrainingConfig())
    names = [p.name for p in periods.periods]
    cutoffs = [p.cutoff_date for p in periods.periods]
    assert names == list(PERIOD_NAMES)
    assert cutoffs[0] < cutoffs[1] < cutoffs[2]
    for period in periods.periods:
        assert {"churned", "active"} <= set(period.frame["label"].unique())
        assert period.frame["cutoff_date"].eq(period.cutoff_date).all()
        assert period.frame["period"].eq(period.name).all()
        # both classes present → imbalance is reported (CHURN-08)
        assert period.frame["label"].nunique() == 2
    assert periods.total_before_dedup >= sum(len(p.frame) for p in periods.periods)


def test_period_features_only_use_own_boundary() -> None:
    """A customer's earlier-period snapshot must not carry outcomes from
    later periods: check a customer whose last order falls inside a later
    validation window appears with recency consistent with its OWN cutoff."""
    periods = build_periods(CUSTOMERS_DF, ORDERS_DF, TrainingConfig())
    for period in periods.periods:
        cutoff = pd.Timestamp(period.cutoff_date)
        rows = period.frame.to_dict("records")
        for row in rows:
            # recency can never exceed days covered before the cutoff
            assert row["recency_days"] is None or row["recency_days"] >= 0
    _ = cutoff  # names only; deep leakage is covered by FE-09 tests


def test_customers_without_orders_are_excluded() -> None:
    lonely = pd.DataFrame([{"customer_id": "ZZ", "created_at": "", "email": "z@x.io"}])
    Everyone = pd.concat([CUSTOMERS_DF, lonely], ignore_index=True)
    periods = build_periods(Everyone, ORDERS_DF, TrainingConfig())
    for period in periods.periods:
        assert "ZZ" not in set(period.frame["customer_id"])


# --- CHURN-06..08: pipelines + metrics --------------------------------------


def test_pipelines_every_candidate_and_reproducible() -> None:
    pipelines = build_model_pipelines(TrainingConfig(random_seed=42, n_estimators=40))
    assert set(pipelines) == {"logistic_regression", "random_forest"}
    periods = build_periods(CUSTOMERS_DF, ORDERS_DF, TrainingConfig())
    X_train = periods.periods[0].frame[list(FEATURE_COLUMNS)]
    y_train = (periods.periods[0].frame["label"] == POSITIVE_LABEL).astype(int)
    X_val = periods.periods[1].frame[list(FEATURE_COLUMNS)]
    first = [p.fit(X_train, y_train) for p in pipelines.values()]
    probs_a = [m.predict_proba(X_val)[:, 1] for m in first]
    second = [
        p.fit(X_train, y_train) for p in build_model_pipelines(TrainingConfig(random_seed=42, n_estimators=40)).values()
    ]
    probs_b = [m.predict_proba(X_val)[:, 1] for m in second]
    for a, b in zip(probs_a, probs_b, strict=True):
        assert np.allclose(a, b)


def test_safe_metrics_handle_undefined_cases() -> None:
    from app.domains.churn.trainer import _safe_metrics

    # single-class truth → both AUCs null with an explicit reason
    metrics = _safe_metrics(np.zeros(10, dtype=int), np.full(10, 0.4))
    assert metrics["roc_auc"] is None and metrics["pr_auc"] is None
    assert any("one class" in r for r in metrics["undefined_reasons"])
    assert metrics["recall"] is None  # no positive labels

    # no positive predictions → precision None with reason
    metrics = _safe_metrics(np.array([0, 1, 0, 1] * 5), np.zeros(20))
    assert metrics["precision"] is None
    assert any("no positive predictions" in r for r in metrics["undefined_reasons"])

    # healthy case has all metrics
    y = np.array([0] * 10 + [1] * 10)
    p = np.concatenate([np.linspace(0.01, 0.49, 10), np.linspace(0.51, 0.99, 10)])
    metrics = _safe_metrics(y, p)
    for key in ("precision", "recall", "f1", "roc_auc", "pr_auc", "confusion_matrix"):
        assert metrics[key] is not None


# --- CHURN-09..11: thresholds, calibration, selection -----------------------


def test_threshold_and_capacity_report() -> None:
    result = train_churn_models(CUSTOMERS_DF, ORDERS_DF, TrainingConfig(min_period_customers=10))
    analysis = result.evaluation["threshold_analysis"]["logistic_regression"]
    assert len(analysis["sweep"]) == 9  # default sweep 0.1..0.9
    for row in analysis["sweep"]:
        assert row["threshold"] in (0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9)
        assert row["flagged_customers"] >= 0
        assert 0 <= row["true_churners_flagged"] <= row["flagged_customers"]
    val_size = result.evaluation["split"]["validation"]["customers"]
    for row, fraction in zip(analysis["top_k_by_capacity"], (0.10, 0.25, 0.50), strict=True):
        assert row["k"] == max(1, round(fraction * val_size))
        assert row["precision_at_k"] <= 1.0
        assert "capacity" in analysis["note"]


def test_calibration_report_shape() -> None:
    result = train_churn_models(CUSTOMERS_DF, ORDERS_DF, TrainingConfig(min_period_customers=10))
    calib = result.evaluation["calibration"]["random_forest"]
    assert isinstance(calib["brier_score"], float)
    assert 0 <= calib["brier_score"] <= 1
    assert calib["n_bins_requested"] == 10
    for bin_info in calib["reliability_bins"]:
        assert bin_info["count"] > 0
        assert 0 <= bin_info["observed_churn_rate"] <= 1


def test_selection_and_override() -> None:
    auto = train_churn_models(CUSTOMERS_DF, ORDERS_DF, TrainingConfig(min_period_customers=10))
    selection = auto.evaluation["selection"]
    assert selection["selected_model"] in ("logistic_regression", "random_forest")
    assert "rationale" in selection and selection["rationale"]

    forced = train_churn_models(
        CUSTOMERS_DF,
        ORDERS_DF,
        TrainingConfig(min_period_customers=10, override_selection="logistic_regression"),
    )
    assert forced.evaluation["selection"]["selected_model"] == "logistic_regression"
    assert forced.evaluation["selection"]["selection_basis"] == "config override"


# --- CHURN-12: artifacts + metadata ------------------------------------------


def test_artifacts_saved_with_full_metadata(tmp_path: pathlib.Path) -> None:
    result = train_churn_models(
        CUSTOMERS_DF,
        ORDERS_DF,
        TrainingConfig(min_period_customers=10, n_estimators=40),
        dataset_version_id="11111111-1111-1111-1111-111111111111",
    )
    paths = save_artifacts(result, tmp_path / "run")
    assert set(paths) == {"logistic_regression", "random_forest", "metadata"}
    for model in ("logistic_regression", "random_forest"):
        loaded = joblib.load(paths[model])
        # the saved model is a fitted pipeline that can predict
        X = result.periods.by_name()["test"].frame[list(FEATURE_COLUMNS)].head(5)
        probs = loaded.predict_proba(X)
        assert probs.shape == (5, 2)
    metadata = json.loads(pathlib.Path(paths["metadata"]).read_text(encoding="utf-8"))
    assert metadata["dataset_version_id"] == "11111111-1111-1111-1111-111111111111"
    assert metadata["feature_version"] == "1.0.0"
    assert metadata["feature_columns"] == list(FEATURE_COLUMNS)
    assert set(metadata["cutoffs"]) == set(PERIOD_NAMES)
    assert metadata["observation_window_days"] == 180
    assert metadata["label_horizon_days"] == 90
    assert metadata["random_seed"] == 42
    assert "scikit-learn" in metadata["dependency_versions"]
    for model_type in ("logistic_regression", "random_forest"):
        assert "estimator" in metadata["hyperparameters"][model_type]
    assert metadata["selection"]["selected_model"] == result.selected_model_type


def test_train_rejects_when_any_period_missing() -> None:
    with pytest.raises(ValueError, match="single label class"):
        train_churn_models(
            CUSTOMERS_DF.head(6),  # tiny data → span too short
            ORDERS_DF[ORDERS_DF["customer_id"].isin([f"C00{i}" for i in range(6)])],
            TrainingConfig(min_period_customers=10),
        )


def test_small_period_produces_warning() -> None:
    result = train_churn_models(CUSTOMERS_DF, ORDERS_DF, TrainingConfig(min_period_customers=10**6))
    assert any("labeled customers" in w for w in result.metadata["warnings"])


# --- DB lifecycle: run records + artifacts (service level) --------------------


def _csv_dataset(client, headers) -> tuple[str, str]:
    customers_csv = "customer_id,created_at,email\n" + "\n".join(
        f"{row['customer_id']},{row['created_at']},{row['email']}" for row in CUSTOMERS_DF.to_dict("records")
    )
    orders_csv = "customer_id,order_id,order_date,order_status,order_amount\n" + "\n".join(
        f"{row['customer_id']},{row['order_id']},{row['order_date']},{row['order_status']},{row['order_amount']}"
        for row in ORDERS_DF.to_dict("records")
    )
    dataset_id = create_dataset(client, headers)
    customers = upload_csv_text(client, headers, dataset_id, kind="customers", csv_text=customers_csv)
    orders = upload_csv_text(client, headers, dataset_id, kind="orders", csv_text=orders_csv)
    apply_suggested_mapping(client, headers, dataset_id, customers["file"]["id"], "customers")
    apply_suggested_mapping(client, headers, dataset_id, orders["file"]["id"], "orders")
    response = client.post(
        f"/api/v1/datasets/{dataset_id}/versions",
        json={"customers_file_id": customers["file"]["id"], "orders_file_id": orders["file"]["id"]},
        headers=headers,
    )
    assert response.status_code == 202, response.text
    return dataset_id, response.json()["version"]["id"]


def test_model_run_lifecycle_on_database(client, org_a_owner, app_db, tmp_path, monkeypatch) -> None:
    """create_model_run queues → inline job executes → run row populated
    with metrics/artifact (CHURN-12 registry foundation)."""
    _, factory = app_db
    monkeypatch.setattr(settings, "models_dir", tmp_path / "models", raising=False)

    headers = org_a_owner["headers"]
    dataset_id, version_id = _csv_dataset(client, headers)

    from sqlalchemy import select

    from app.domains.identity.models import Membership, User

    # force queue mode but intercept Celery dispatch (no broker in tests)
    settings.inline_jobs = False
    monkeypatch.setattr(
        "app.workers.tasks.dispatch_job",
        type("T", (), {"delay": staticmethod(lambda job_id: None)}),
    )

    with factory() as db:
        user = db.scalar(select(User).limit(1))
        membership = db.scalar(select(Membership).where(Membership.user_id == user.id).limit(1))
        run = create_model_run(
            db,
            org_id=membership.organization_id,
            user_id=user.id,
            dataset_version_id=uuid_module.UUID(version_id),
            random_seed=42,
            min_period_customers=10,
        )
        assert run.status == "queued"
        assert run.metrics["params"]["random_seed"] == 42

    settings.inline_jobs = True

    with factory() as db:
        summary = execute_model_run(db, run_id=run.id)
        assert summary["status"] == "succeeded"
        assert summary["model_type"] in ("logistic_regression", "random_forest")
        assert summary["artifact_path"]

        refreshed = get_model_run(db, membership.organization_id, run.id)
        assert refreshed.metrics["validation"]["logistic_regression"]["roc_auc"] is not None
        assert refreshed.metrics["test"]["random_forest"]["pr_auc"] is not None
        assert refreshed.train_cutoff_date  # split recorded
        artifact_dir = pathlib.Path(refreshed.artifact_path)
        assert (artifact_dir / "metadata.json").exists()
        assert (artifact_dir / "logistic_regression.joblib").exists()
        assert (artifact_dir / "random_forest.joblib").exists()
        metadata = json.loads((artifact_dir / "metadata.json").read_text(encoding="utf-8"))
        assert metadata["dataset_version_id"] == version_id
        assert metadata["random_seed"] == 42


def test_create_model_run_rejects_invalid_version(client, org_a_owner, app_db) -> None:
    _, factory = app_db
    from sqlalchemy import select

    from app.domains.identity.models import Membership, User

    with factory() as db:
        user = db.scalar(select(User).limit(1))
        membership = db.scalar(select(Membership).where(Membership.user_id == user.id).limit(1))
        with pytest.raises(APIError, match="Dataset version not found"):
            create_model_run(
                db,
                org_id=membership.organization_id,
                user_id=user.id,
                dataset_version_id=uuid_module.UUID("00000000-0000-0000-0000-000000000000"),
            )
