"""CHURN-13..17 acceptance tests: batch scoring, risk bands, explanations.

Reuses the synthetic validated dataset helpers from test_training so a
real training run feeds a real scoring run end-to-end.
"""

from __future__ import annotations

import pathlib
import uuid as uuid_module

import pandas as pd
import pytest
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from app.core.config import settings
from app.core.errors import APIError
from app.domains.churn.scoring import (
    ScoringConfig,
    explain_customer,
    global_importance,
    risk_band,
    score_eligible_customers,
    scoring_cutoff,
    validate_band_thresholds,
)
from tests.test_training import CUSTOMERS_DF, ORDERS_DF, _csv_dataset


def _features_frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "recency_days": [10.0, 40.0, 70.0, 100.0],
            "frequency": [3, 2, 1, 0],
            "monetary_value": [300, 200, 100, 0],
            "avg_order_value": [100, 100, 100, 0],
            "tenure_days": [200, 180, 150, 100],
            "orders_30d": [1, 0, 0, 0],
            "orders_90d": [2, 1, 0, 0],
            "monetary_30d": [100, 0, 0, 0],
            "monetary_90d": [200, 100, 0, 0],
            "avg_purchase_interval_days": [30, 40, 50, 60],
            "discount_total": [0, 5, 10, 20],
        }
    )


def _tiny_pipeline() -> Pipeline:
    return Pipeline(
        steps=[
            ("imputer", SimpleImputer(strategy="median")),
            ("scaler", StandardScaler()),
            ("estimator", LogisticRegression(max_iter=100)),
        ]
    )


# --- CHURN-14: configurable risk bands ---------------------------------------


def test_risk_band_boundaries_match_defaults() -> None:
    # Default display bands: Low < 0.30, Medium 0.30–0.69, High >= 0.70.
    assert risk_band(0.29, low_band_max=0.30, high_band_min=0.70) == "low"
    assert risk_band(0.30, low_band_max=0.30, high_band_min=0.70) == "medium"
    assert risk_band(0.699, low_band_max=0.30, high_band_min=0.70) == "medium"
    assert risk_band(0.70, low_band_max=0.30, high_band_min=0.70) == "high"
    assert risk_band(0.0, low_band_max=0.30, high_band_min=0.70) == "low"


def test_band_thresholds_are_configuration_not_hardcoded() -> None:
    # Non-default thresholds re-band the same probability (config-driven).
    assert risk_band(0.35, low_band_max=0.40, high_band_min=0.80) == "low"
    assert risk_band(0.55, low_band_max=0.40, high_band_min=0.80) == "medium"
    assert risk_band(0.85, low_band_max=0.40, high_band_min=0.80) == "high"
    # Settings expose the defaults; they are read at request time, not baked in.
    assert settings.risk_band_low_max == 0.30
    assert settings.risk_band_high_min == 0.70
    with pytest.raises(ValueError, match="0 <= low_band_max"):
        validate_band_thresholds(0.8, 0.2)
    with pytest.raises(ValueError, match="0 <= low_band_max"):
        validate_band_thresholds(1.2, 0.9)
    assert validate_band_thresholds(0.25, 0.25) == (0.25, 0.25)


def test_scoring_config_defaults() -> None:
    config = ScoringConfig()
    assert config.low_band_max == 0.30
    assert config.high_band_min == 0.70
    assert config.observation_window_days == 180


# --- CHURN-13: batch scoring for eligible active customers --------------------


def test_scoring_one_prediction_per_eligible_customer() -> None:
    pipeline = _tiny_pipeline().fit(_features_frame(), [0, 1, 0, 1])
    scored, cutoff = score_eligible_customers(CUSTOMERS_DF, ORDERS_DF, pipeline, observation_window_days=180)
    # Eligible = completed order inside the 180-day window ending at the
    # scoring cutoff (same eligibility contract as labeling); customers
    # without such history are never fabricated (CHURN-02 contract).
    cutoff_ts = pd.Timestamp(scoring_cutoff(ORDERS_DF))
    window_start = cutoff_ts - pd.Timedelta(days=180)
    completed = pd.to_datetime(ORDERS_DF["order_date"])
    last_per_customer = ORDERS_DF.assign(_d=completed).groupby("customer_id")["_d"].max()
    eligible_ids = set(last_per_customer[(last_per_customer >= window_start) & (last_per_customer <= cutoff_ts)].index)
    assert set(scored["customer_id"]) == eligible_ids
    assert set(scored.columns) >= {"customer_id", "churn_probability"}
    assert scored["churn_probability"].between(0, 1).all()
    assert len(scored) == len(set(scored["customer_id"]))  # one per customer
    assert cutoff == scoring_cutoff(ORDERS_DF)

    lonely = pd.DataFrame([{"customer_id": "ZZ", "created_at": "", "email": "z@x.io"}])
    patched = pd.concat([CUSTOMERS_DF, lonely], ignore_index=True)
    scored2, _ = score_eligible_customers(patched, ORDERS_DF, pipeline, observation_window_days=180)
    assert "ZZ" not in set(scored2["customer_id"])

    # deterministic re-scoring
    again, _ = score_eligible_customers(CUSTOMERS_DF, ORDERS_DF, pipeline, observation_window_days=180)
    assert again["churn_probability"].tolist() == scored["churn_probability"].tolist()


def test_scoring_rejects_empty_population() -> None:
    orders = ORDERS_DF.copy()
    orders["order_date"] = ""  # no dated orders → no cutoff, no population
    with pytest.raises(ValueError, match="dated orders"):
        score_eligible_customers(CUSTOMERS_DF[["customer_id", "created_at", "email"]], orders, _tiny_pipeline())


# --- CHURN-15: global importance ----------------------------------------------


def test_global_importance_is_model_appropriate_and_labeled(tmp_path: pathlib.Path) -> None:
    trainer = pytest.importorskip("app.domains.churn.trainer")
    result = trainer.train_churn_models(
        CUSTOMERS_DF, ORDERS_DF, trainer.TrainingConfig(min_period_customers=10, n_estimators=40)
    )
    report = result.metadata["global_importance"]
    assert set(report) == {"logistic_regression", "random_forest"}

    lr, rf = report["logistic_regression"], report["random_forest"]
    assert lr["method"] == "standardized_linear_coefficients"
    assert rf["method"] == "random_forest_impurity_importance"
    for model_report in (lr, rf):
        # CHURN-15: clearly association, explicitly not causal.
        assert "association" in model_report["note"]
        assert "not causal" in model_report["note"]
        ranked = [row["importance"] for row in model_report["ranking"]]
        assert ranked == sorted(ranked, key=abs, reverse=True)
        assert len(model_report["ranking"]) == 11


def test_global_importance_linear_keeps_sign() -> None:
    pipeline = _tiny_pipeline().fit(_features_frame(), [0, 1, 0, 1])
    report = global_importance(pipeline, "logistic_regression")
    assert report["model_type"] == "logistic_regression"
    for row in report["ranking"]:
        assert row["signed"] is not None  # LR exposes direction


# --- CHURN-16: customer-level explanations ------------------------------------


def test_customer_explanations_wording_and_shape() -> None:
    pipeline = _tiny_pipeline().fit(_features_frame(), [0, 1, 0, 1])
    population, cutoff = score_eligible_customers(CUSTOMERS_DF, ORDERS_DF, pipeline, observation_window_days=180)
    assert cutoff  # scoring cutoff propagates
    features_frame = population.drop(columns=["customer_id", "churn_probability"])
    for _, customer in population.head(5).iterrows():
        explanation = explain_customer(pipeline, "logistic_regression", features_frame, customer)
        assert explanation["exact"] is True
        assert explanation["method"] == "standardized_linear_contribution"
        assert len(explanation["top_signals"]) == 3
        for signal in explanation["top_signals"]:
            assert signal["direction"] in ("higher_risk", "lower_risk")
            assert signal["feature"] in features_frame.columns
        # CHURN-16: no causal wording anywhere in the payload
        text = str(explanation).lower()
        for banned in ("cause of churn", "causes churn", "will churn because", "drives churn because"):
            assert banned not in text
        assert "association" in explanation["disclaimer"]
        assert any("not causal" in limitation for limitation in explanation["limitations"])


# --- DB lifecycle: scoring run persisted + traceable (CHURN-13/17) -------------


@pytest.fixture()
def _trained_run(client, org_a_owner, app_db, tmp_path, monkeypatch):
    """Upload + validate the synthetic dataset and run one training job."""
    _, factory = app_db
    monkeypatch.setattr(settings, "models_dir", tmp_path / "models", raising=False)

    headers = org_a_owner["headers"]
    dataset_id, version_id = _csv_dataset(client, headers)

    from sqlalchemy import select

    from app.domains.churn.service import create_model_run
    from app.domains.identity.models import Membership, User

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
        assert run.status == "succeeded"  # inline job ran synchronously
        return {"run_id": run.id, "org_id": membership.organization_id, "user_id": user.id}


def test_score_run_lifecycle_on_database(client, org_a_owner, app_db, _trained_run) -> None:
    """train → score: scores persist with bands + explanations, referencing
    the immutable model run (CHURN-13, CHURN-14, CHURN-16, CHURN-17)."""
    ids = _trained_run
    _, factory = app_db

    from sqlalchemy import select

    from app.domains.churn.models import ChurnScore
    from app.domains.churn.service import create_score_run, get_customer_score, list_scores

    with factory() as db:
        job = create_score_run(db, org_id=ids["org_id"], user_id=ids["user_id"], model_run_id=ids["run_id"])
        # scoring ran inline; the immutable Job row is the analysis-run record
        assert job.job_type == "score_churn_model"
        assert job.status == "succeeded"

    with factory() as db:
        scores, total = list_scores(db, ids["org_id"], ids["run_id"])
        # one score per eligible customer (those with a completed order in
        # the 180-day window ending at the scoring cutoff)
        cutoff_ts = pd.Timestamp(scoring_cutoff(ORDERS_DF))
        window_start = cutoff_ts - pd.Timedelta(days=180)
        completed = pd.to_datetime(ORDERS_DF["order_date"])
        last_per_customer = ORDERS_DF.assign(_d=completed).groupby("customer_id")["_d"].max()
        eligible_count = int(((last_per_customer >= window_start) & (last_per_customer <= cutoff_ts)).sum())
        assert total == eligible_count
        assert len(scores) == min(25, total)  # default page size
        bands = {row.risk_band for row in scores}
        assert bands <= {"low", "medium", "high"}
        for row in scores:
            assert row.explanation is not None
            assert row.cutoff_date
            # CHURN-17: every score references the model run (model version)
            assert row.model_run_id == ids["run_id"]

    with factory() as db:
        high_scores, high_total = list_scores(db, ids["org_id"], ids["run_id"], risk_band="high")
        all_high = db.scalars(
            select(ChurnScore).where(ChurnScore.model_run_id == ids["run_id"], ChurnScore.risk_band == "high")
        ).all()
        assert high_total == len(all_high)
        assert all(row.risk_band == "high" for row in high_scores)

    with factory() as db:
        first = list_scores(db, ids["org_id"], ids["run_id"])[0][0]
        view = get_customer_score(db, ids["org_id"], model_run_id=ids["run_id"], customer_id=first.customer_id)
        assert view["churn_probability"] == first.churn_probability
        assert view["explanation"]["top_signals"]
        assert "association" in str(view["explanation"])

    # re-scoring with other thresholds replaces the previous score set
    with factory() as db:
        create_score_run(
            db,
            org_id=ids["org_id"],
            user_id=ids["user_id"],
            model_run_id=ids["run_id"],
            low_band_max=0.2,
            high_band_min=0.6,
        )
    with factory() as db:
        scores, total = list_scores(db, ids["org_id"], ids["run_id"])
        assert total == eligible_count  # replaced, not duplicated
        customer_ids = [row.customer_id for row in scores]
    assert len(customer_ids) == len(set(customer_ids))


def test_create_score_run_rejects_unsucceeded_run(client, org_a_owner, app_db, tmp_path, monkeypatch) -> None:
    _, factory = app_db
    monkeypatch.setattr(settings, "models_dir", tmp_path / "models", raising=False)
    headers = org_a_owner["headers"]
    dataset_id, version_id = _csv_dataset(client, headers)

    from sqlalchemy import select

    from app.domains.churn.models import MODEL_RUN_QUEUED, ChurnModelRun
    from app.domains.churn.service import create_model_run, create_score_run
    from app.domains.identity.models import Membership, User

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
        run_id, org_id, user_id = run.id, membership.organization_id, user.id

    with factory() as db:
        # downgrade the run again to exercise the queue/failed guard
        row = db.scalar(select(ChurnModelRun).where(ChurnModelRun.id == run_id))
        row.status = MODEL_RUN_QUEUED
        row.artifact_path = None
        db.flush()
        with pytest.raises(APIError, match="succeeded"):
            create_score_run(db, org_id=org_id, user_id=user_id, model_run_id=run_id)

        # cross-tenant access: unknown org → 404 (never leaks existence)
        with pytest.raises(APIError, match="Model run not found"):
            create_score_run(
                db,
                org_id=uuid_module.UUID("00000000-0000-0000-0000-000000000000"),
                user_id=user_id,
                model_run_id=run_id,
            )


def test_score_run_band_config_recorded(_trained_run, app_db) -> None:
    """CHURN-14: explicit thresholds flow through the payload (Job row),
    not by mutation of global state."""
    ids = _trained_run
    _, factory = app_db

    from app.domains.churn.service import create_score_run

    with factory() as db:
        job = create_score_run(
            db,
            org_id=ids["org_id"],
            user_id=ids["user_id"],
            model_run_id=ids["run_id"],
            low_band_max=0.1,
            high_band_min=0.9,
        )
        assert job.payload["low_band_max"] == 0.1
        assert job.payload["high_band_min"] == 0.9

    with factory() as db:
        job2 = create_score_run(db, org_id=ids["org_id"], user_id=ids["user_id"], model_run_id=ids["run_id"])
        # defaults flow from Settings when not explicit
        assert job2.payload["low_band_max"] == settings.risk_band_low_max
        assert job2.payload["high_band_min"] == settings.risk_band_high_min
