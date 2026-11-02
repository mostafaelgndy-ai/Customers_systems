"""SEG-01..10 acceptance tests: segmentation pipeline + run lifecycle."""

from __future__ import annotations

import json
import pathlib
import uuid as uuid_module

import joblib
import numpy as np
import pandas as pd
import pytest

from app.core.config import settings
from app.core.errors import APIError
from app.domains.segmentation.pipeline import (
    DEFAULT_K_CANDIDATES,
    SEGMENT_FEATURES,
    SegmentationConfig,
    build_population,
    build_preprocessing,
    build_profiles,
    relabel_segment,
    run_segmentation,
)
from app.domains.segmentation.service import (
    compare_segmentation_runs,
    create_segmentation_run,
    execute_segmentation_run,
    get_segmentation_run,
    list_assignments,
    rename_segment,
)
from tests.test_training import CUSTOMERS_DF, ORDERS_DF, _csv_dataset

# Cross-check that the segmentation feature set matches the shared feature
# pipeline vocabulary and excludes identifiers (SEG-01).
FEATURE_COLUMNS = SEGMENT_FEATURES


def test_segmentation_feature_selection_excludes_identifiers() -> None:
    banned = {"customer_id", "email", "order_id", "created_at", "cutoff_date", "feature_version"}
    assert not banned & set(SEGMENT_FEATURES)
    # All chosen features are behavioral and exist in the shared pipeline schema
    from app.domains.features.pipeline import feature_schema

    schema = feature_schema()
    missing = [feature for feature in SEGMENT_FEATURES if feature not in schema]
    assert not missing, f"Undocumented features: {missing}"


def test_population_excludes_customers_without_orders() -> None:
    lonely = pd.DataFrame([{"customer_id": "ZZ", "created_at": "", "email": "z@x.io"}])
    population = build_population(
        pd.concat([CUSTOMERS_DF, lonely], ignore_index=True),
        ORDERS_DF,
        SegmentationConfig(),
    )
    assert "ZZ" not in set(population.frame["customer_id"])
    assert list(population.frame.columns[:1]) == ["customer_id"]
    assert population.cutoff_date


# --- SEG-02/03: preprocessing fit + persisted with the model --------------------


def test_preprocessing_persisted_and_fit_on_population(tmp_path: pathlib.Path) -> None:
    population = build_population(CUSTOMERS_DF, ORDERS_DF, SegmentationConfig())
    preprocessing = build_preprocessing()
    X = population.frame[list(SEGMENT_FEATURES)]
    X_scaled = preprocessing.fit_transform(X)
    # scaler fitted on THIS run's imputed population, reused consistently
    # (SimpleImputer fills NaNs first, so compare against the imputed matrix)
    X_imputed = preprocessing.named_steps["imputer"].transform(X)
    assert np.allclose(
        preprocessing.named_steps["scaler"].mean_,
        np.nanmean(X_imputed, axis=0),
    )

    result = run_segmentation(CUSTOMERS_DF, ORDERS_DF, SegmentationConfig())
    directory = tmp_path / "artifacts"
    directory.mkdir()
    joblib.dump(result.preprocessing, directory / "preprocessing.joblib")
    joblib.dump(result.kmeans, directory / "kmeans.joblib")
    loaded_pre = joblib.load(directory / "preprocessing.joblib")
    loaded_model = joblib.load(directory / "kmeans.joblib")
    assert np.allclose(loaded_pre.transform(X), X_scaled)
    labels = loaded_model.predict(X_scaled)
    assert list(labels) == list(result.labels)  # reproducible from persisted artifacts


# --- SEG-04/05: seeded K-Means + bounded candidate sweep -----------------------


def test_kmeans_seeded_and_candidates_reported() -> None:
    config = SegmentationConfig(k_candidates=(2, 3, 4))
    first = run_segmentation(CUSTOMERS_DF, ORDERS_DF, config)
    second = run_segmentation(CUSTOMERS_DF, ORDERS_DF, config)

    # determinism for the same seed (SEG-04)
    assert first.labels == second.labels
    assert first.selected_k == second.selected_k

    assert [row["k"] for row in first.candidate_report] == [2, 3, 4]
    for row in first.candidate_report:
        assert sum(row["cluster_sizes"]) == len(first.labels)
        assert 0 < len(row["cluster_sizes"]) == row["k"]
        assert row["silhouette"] is None or -1 <= row["silhouette"] <= 1
    assert first.selected_k in (2, 3, 4)


def test_candidates_bounded_default() -> None:
    assert DEFAULT_K_CANDIDATES == (2, 3, 4, 5, 6)


# --- SEG-06: tiny/degenerate clusters emit warnings ----------------------------


def test_tiny_cluster_flagged_with_warning() -> None:
    # An extreme outlier row creates a one-customer cluster for many k values.
    outlier = pd.DataFrame(
        [
            {
                "customer_id": "EXTREME",
                "created_at": "2020-01-01",
                "email": "x@x.io",
            }
        ]
    )
    extreme_orders = pd.DataFrame(
        [
            {
                "customer_id": "EXTREME",
                "order_id": "EX1",
                "order_date": "2025-11-01",
                "order_status": "completed",
                "order_amount": 999999.0,
            },
            {
                "customer_id": "EXTREME",
                "order_id": "EX2",
                "order_date": "2025-11-15",
                "order_status": "completed",
                "order_amount": 888888.0,
            },
        ]
    )
    patched_orders = pd.concat([ORDERS_DF, extreme_orders], ignore_index=True)
    result = run_segmentation(
        pd.concat([CUSTOMERS_DF, outlier], ignore_index=True),
        patched_orders,
        SegmentationConfig(),
    )
    combined = " ".join(result.warnings)
    assert "below the minimum size" in combined or "caution" in combined
    report = {row["k"]: row for row in result.candidate_report}
    assert any(row["tiny_clusters"] for row in report.values())


# --- SEG-07/08: profiles + editable descriptive labels -------------------------


def test_profiles_have_counts_means_medians_and_labels() -> None:
    result = run_segmentation(CUSTOMERS_DF, ORDERS_DF, SegmentationConfig())
    profiles, labels = build_profiles(result)
    assert {p["cluster_id"] for p in profiles} == set(range(result.selected_k))
    total = sum(p["count"] for p in profiles)
    assert total == len(result.labels)
    for profile in profiles:
        assert profile["count"] >= 1
        assert 0 < profile["share"] <= 1
        for feature in SEGMENT_FEATURES:
            stats = profile[feature]
            assert {"mean", "median", "p25", "p75", "std"} <= set(stats)
        # label from profile stats, never an arbitrary cluster id
        assert profile["descriptive_label"] and profile["descriptive_label"] != str(profile["cluster_id"])
        assert profile["label_editable"] is True
    assert set(labels) == set(range(result.selected_k))


def test_descriptive_labels_reflect_profile_tiers() -> None:
    result = run_segmentation(CUSTOMERS_DF, ORDERS_DF, SegmentationConfig())
    profiles, _ = build_profiles(result)
    for profile in profiles:
        label = profile["descriptive_label"]
        # tiered wording comes from value/frequency/recency comparisons
        tiers = label.split(" · ")
        assert tiers
        for tier in tiers:
            assert tier in {
                "low value",
                "high value",
                "infrequent",
                "frequent",
                "recent",
                "lapsed",
                "steady recency",
            }


def test_segment_labels_are_editable() -> None:
    profiles = [
        {"cluster_id": 0, "descriptive_label": "high value · frequent · recent"},
        {"cluster_id": 1, "descriptive_label": "low value · infrequent · lapsed"},
    ]
    relabel_segment(profiles, 1, "win-back candidates")
    assert profiles[1]["descriptive_label"] == "win-back candidates"
    with pytest.raises(KeyError):
        relabel_segment(profiles, 7, "nobody")
    with pytest.raises(ValueError):
        relabel_segment(profiles, 0, "   ")


# --- SEG-11: DBSCAN alternative honestly evaluated -----------------------------


def test_dbscan_evaluation_recorded_and_kmeans_retained() -> None:
    result = run_segmentation(CUSTOMERS_DF, ORDERS_DF, SegmentationConfig())
    report = result.dbscan_report
    assert report["method"] == "DBSCAN"
    assert "retained_method" in report and report["retained_method"] == "kmeans"
    assert "noise" in report["decision_note"]
    assert 0 <= report["noise_share"] <= 1


# --- SEG-09: lifecycle persists assignments + artifacts ------------------------


def test_segmentation_run_lifecycle_on_database(client, org_a_owner, app_db, tmp_path, monkeypatch) -> None:
    _, factory = app_db
    monkeypatch.setattr(settings, "models_dir", tmp_path / "models", raising=False)

    headers = org_a_owner["headers"]
    dataset_id, version_id = _csv_dataset(client, headers)

    from sqlalchemy import select

    from app.domains.identity.models import Membership, User

    with factory() as db:
        user = db.scalar(select(User).limit(1))
        membership = db.scalar(select(Membership).where(Membership.user_id == user.id).limit(1))
        run = create_segmentation_run(
            db,
            org_id=membership.organization_id,
            user_id=user.id,
            dataset_version_id=uuid_module.UUID(version_id),
            k_candidates=(2, 3),
            random_seed=42,
        )
        assert run.status == "succeeded"  # inline job ran synchronously
        run_id, org_id = run.id, membership.organization_id

    with factory() as db:
        refreshed = get_segmentation_run(db, org_id, run_id)
        assert refreshed.status == "succeeded"
        assert refreshed.selected_k in (2, 3)
        assert refreshed.cutoff_date
        assert refreshed.candidate_report["candidates"]
        assert refreshed.params["random_seed"] == 42  # SEG-04 recorded
        assert refreshed.params["features"] == list(SEGMENT_FEATURES)  # SEG-01 recorded
        assignments, total = list_assignments(db, org_id, run_id)
        assert total >= 30
        assert all(a.segmentation_run_id == run_id for a in assignments)
        assert all(a.segment_label for a in assignments)
        assert {a.cluster_id for a in assignments} == set(range(refreshed.selected_k))

    with factory() as db:
        artifacts_dir = pathlib.Path(get_segmentation_run(db, org_id, run_id).artifact_path)
        assert (artifacts_dir / "preprocessing.joblib").exists()  # SEG-02/03 persisted
        assert (artifacts_dir / "kmeans.joblib").exists()
        metadata = json.loads((artifacts_dir / "metadata.json").read_text(encoding="utf-8"))
        assert metadata["random_seed"] == 42
        assert metadata["segment_features"] == list(SEGMENT_FEATURES)

    # re-running is idempotent: assigned population replaced, not duplicated
    with factory() as db:
        baseline, baseline_total = list_assignments(db, org_id, run_id)
        execute_segmentation_run(db, run_id=run_id)
    with factory() as db:
        updated = get_segmentation_run(db, org_id, run_id)
        assert updated.status == "succeeded"
        reassignments, re_total = list_assignments(db, org_id, run_id)
        assert re_total == baseline_total  # replaced, not duplicated
        customer_ids = [a.customer_id for a in reassignments]
        assert len(customer_ids) == len(set(customer_ids))


def test_rejects_invalid_version(client, org_a_owner, app_db) -> None:
    _, factory = app_db
    from sqlalchemy import select

    from app.domains.identity.models import Membership, User

    with factory() as db:
        user = db.scalar(select(User).limit(1))
        membership = db.scalar(select(Membership).where(Membership.user_id == user.id).limit(1))
        with pytest.raises(APIError, match="Dataset version not found"):
            create_segmentation_run(
                db,
                org_id=membership.organization_id,
                user_id=user.id,
                dataset_version_id=uuid_module.UUID("00000000-0000-0000-0000-000000000000"),
            )


def test_rename_segment_updates_run_and_assignments(client, org_a_owner, app_db, tmp_path, monkeypatch) -> None:
    from sqlalchemy import select

    from app.domains.identity.models import Membership, User

    _, factory = app_db
    monkeypatch.setattr(settings, "models_dir", tmp_path / "models", raising=False)
    headers = org_a_owner["headers"]
    dataset_id, version_id = _csv_dataset(client, headers)

    with factory() as db:
        user = db.scalar(select(User).limit(1))
        membership = db.scalar(select(Membership).where(Membership.user_id == user.id).limit(1))
        run = create_segmentation_run(
            db,
            org_id=membership.organization_id,
            user_id=user.id,
            dataset_version_id=uuid_module.UUID(version_id),
            k_candidates=(3,),
        )
        run_id, org_id = run.id, membership.organization_id

    with factory() as db:
        result = rename_segment(db, org_id, run_id, cluster_id=0, new_label="champions")
        assert result["updated_assignments"] >= 1
        refreshed = get_segmentation_run(db, org_id, run_id)
        labels = {profile["cluster_id"]: profile["descriptive_label"] for profile in refreshed.profiles["segments"]}
        assert labels[0] == "champions"
        assignments, _ = list_assignments(db, org_id, run_id, cluster_id=0)
        assert all(assignment.segment_label == "champions" for assignment in assignments)


# --- SEG-10: comparison report across runs --------------------------------------


def test_comparison_report_with_caveats(client, org_a_owner, app_db, tmp_path, monkeypatch) -> None:
    from sqlalchemy import select

    from app.domains.identity.models import Membership, User

    _, factory = app_db
    monkeypatch.setattr(settings, "models_dir", tmp_path / "models", raising=False)
    headers = org_a_owner["headers"]
    dataset_id, version_id = _csv_dataset(client, headers)

    with factory() as db:
        user = db.scalar(select(User).limit(1))
        membership = db.scalar(select(Membership).where(Membership.user_id == user.id).limit(1))
        run_a = create_segmentation_run(
            db,
            org_id=membership.organization_id,
            user_id=user.id,
            dataset_version_id=uuid_module.UUID(version_id),
            k_candidates=(3,),
            random_seed=42,
        )
        run_b = create_segmentation_run(
            db,
            org_id=membership.organization_id,
            user_id=user.id,
            dataset_version_id=uuid_module.UUID(version_id),
            k_candidates=(3,),
            random_seed=43,  # different seed: honest comparison rather than identity
        )
        report = compare_segmentation_runs(db, membership.organization_id, run_a.id, run_b.id)
        assert report["run_a"] == str(run_a.id) and report["run_b"] == str(run_b.id)
        assert report["segment_share_deltas"]
        assert 0 <= report["max_share_shift"] <= 1
        assert any("not" in caveat and "significance" in caveat for caveat in report["caveats"])

        with pytest.raises(APIError, match="not found"):
            compare_segmentation_runs(
                db,
                membership.organization_id,
                run_a.id,
                uuid_module.UUID("00000000-0000-0000-0000-000000000000"),
            )
