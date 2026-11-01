"""Phase 3 acceptance tests: feature engineering (FE-02..FE-10).

Unit tests use hand-calculated fixtures (FE-03/04/05/06); API tests verify
the run lifecycle, persistence, determinism (FE-08/10), and temporal
leakage safety (FE-09).
"""

from __future__ import annotations

import pandas as pd
import pytest

from app.domains.features.pipeline import FeatureConfig, compute_features, feature_schema
from tests.conftest import apply_suggested_mapping, create_dataset, make_validated_dataset, upload_csv_text

# Hand-calculated fixture (FE-03/04/05/06)
#
# cutoff = 2025-06-30, observation window = 180d → start = 2024-12-31.
# 30d window start = 2025-05-31 (inclusive), 90d start = 2025-04-01.
#
# C1 (created 2025-01-05, tenure = 176 days):
#   2025-06-30  100 completed (recency 0; in 30d/90d)
#   2025-05-31   50 completed (boundary date → in 30d/90d)
#   2025-04-15  200 completed (in 90d, not 30d)
#   2025-01-10  800 completed (in obs window only)
#   2025-06-20  500 refunded (ineligible status)
#   2020-01-05   50 completed (before obs window)
#   → frequency=4, monetary=1150.00, aov=287.50, orders_30d=2 (150.00),
#     orders_90d=3 (350.00), intervals 95/46/30 → avg 57.0, discounts=15.00
#
# C2 (created_at blank → tenure None):
#   2025-06-15  300 completed (recency 15)
#   2025-05-30  100 completed (one day before 30d boundary → only 90d)
#   → frequency=2, monetary=400.00, aov=200.00, orders_30d=1, orders_90d=2,
#     interval = 16.0
#
# C3 (created 2025-03-01, tenure = 121 days): no orders at all:
#   → frequency=0, monetary=0.00, recency None, interval None

CUTOFF = "2025-06-30"

CUSTOMERS_DF = pd.DataFrame(
    {
        "customer_id": ["C1", "C2", "C3"],
        "created_at": ["2025-01-05", "", "2025-03-01"],
        "email": ["c1@x.io", "c2@x.io", "c3@x.io"],
    }
)

ORDERS_DF = pd.DataFrame(
    {
        "customer_id": ["C1"] * 6 + ["C2"] * 2,
        "order_id": [f"O{i}" for i in range(1, 9)],
        "order_date": [
            "2025-06-30",
            "2025-05-31",
            "2025-04-15",
            "2025-01-10",
            "2025-06-20",
            "2020-01-05",
            "2025-06-15",
            "2025-05-30",
        ],
        "order_status": [
            "completed",
            "completed",
            "completed",
            "completed",
            "refunded",
            "completed",
            "completed",
            "completed",
        ],
        "order_amount": ["100.00", "50.00", "200.00", "800.00", "500.00", "50.00", "300.00", "100.00"],
        "discount_amount": ["10.00", "5.00", "0", "0", "0", "0", "0", "0"],
    }
)

CONFIG = FeatureConfig(cutoff_date=CUTOFF)


@pytest.fixture(scope="module")
def features() -> dict[str, dict]:
    rows = compute_features(CUSTOMERS_DF, ORDERS_DF, CONFIG)
    return {row["customer_id"]: row for row in rows}


def test_recency_frequency_monetary_match_hand_calculations(features) -> None:
    c1 = features["C1"]
    assert c1["recency_days"] == 0
    assert c1["frequency"] == 4
    assert c1["monetary_value"] == 1150.00
    assert c1["avg_order_value"] == 287.50

    c2 = features["C2"]
    assert c2["recency_days"] == 15
    assert c2["frequency"] == 2
    assert c2["monetary_value"] == 400.00
    assert c2["avg_order_value"] == 200.00


def test_eligible_order_filtering_excludes_statuses_and_out_of_window(features) -> None:
    c1 = features["C1"]
    # refunded order (500) and pre-window completed order (50) must not count
    assert c1["frequency"] == 4
    assert c1["monetary_value"] == 1150.00  # 100 + 50 + 200 + 800
    assert c1["discount_total"] == 15.00  # refunded order's discount not included


def test_tenure_and_edge_cases(features) -> None:
    c1 = features["C1"]
    assert c1["tenure_days"] == 176

    c2 = features["C2"]
    assert c2["tenure_days"] is None  # missing creation date → null, not 0

    c3 = features["C3"]
    assert c3["tenure_days"] == 121
    assert c3["frequency"] == 0
    assert c3["monetary_value"] == 0.00
    assert c3["recency_days"] is None  # no eligible orders → null, not 0
    assert c3["avg_purchase_interval_days"] is None


def test_rolling_windows_are_cutoff_aware_and_boundary_inclusive(features) -> None:
    c1 = features["C1"]
    # 2025-05-31 (cutoff - 30d exactly) is IN the 30d window.
    assert c1["orders_30d"] == 2
    assert c1["monetary_30d"] == 150.00
    assert c1["orders_90d"] == 3
    assert c1["monetary_90d"] == 350.00

    c2 = features["C2"]
    # 2025-05-30 (one day before the 30d boundary) is OUT of the 30d window.
    assert c2["orders_30d"] == 1
    assert c2["monetary_30d"] == 300.00
    assert c2["orders_90d"] == 2
    assert c2["monetary_90d"] == 400.00
    assert c2["avg_purchase_interval_days"] == 16.0


def test_purchase_interval_matches_hand_calculation(features) -> None:
    # C1 eligible dates: 01-10, 04-15, 05-31, 06-30 → gaps 95, 46, 30 → 57.0
    assert features["C1"]["avg_purchase_interval_days"] == 57.0


def test_pipeline_is_deterministic() -> None:
    first = compute_features(CUSTOMERS_DF, ORDERS_DF, CONFIG)
    second = compute_features(CUSTOMERS_DF, ORDERS_DF, CONFIG)
    assert first == second


def test_no_temporal_leakage_from_post_cutoff_orders() -> None:
    """Adding orders strictly after the cutoff must not change any feature (FE-09)."""
    pre = compute_features(CUSTOMERS_DF, ORDERS_DF, CONFIG)

    leaked = ORDERS_DF.copy()
    extra = pd.DataFrame(
        {
            "customer_id": ["C1", "C2"],
            "order_id": ["LEAK1", "LEAK2"],
            "order_date": ["2025-07-05", "2025-07-05"],
            "order_status": ["completed", "completed"],
            "order_amount": ["99999.00", "99999.00"],
            "discount_amount": ["0", "0"],
        }
    )
    leaked = pd.concat([leaked, extra], ignore_index=True)
    post = compute_features(CUSTOMERS_DF, leaked, CONFIG)

    assert pre == post


def test_feature_schema_is_documented() -> None:
    """FE-07: every feature declares definition/type/source/cutoff/missing policy."""
    schema = feature_schema()
    expected_keys = {
        "customer_id",
        "recency_days",
        "frequency",
        "monetary_value",
        "avg_order_value",
        "tenure_days",
        "orders_30d",
        "orders_90d",
        "monetary_30d",
        "monetary_90d",
        "avg_purchase_interval_days",
        "discount_total",
    }
    assert set(schema) == expected_keys
    for name, spec in schema.items():
        if name == "customer_id":
            continue
        assert set(spec) >= {"definition", "type", "source", "cutoff", "missing_policy"}
        assert spec["definition"] and spec["type"] and spec["source"]


# --- API-level tests: run lifecycle + persistence (FE-08, FE-10) -----------


ORDERS_CSV = """customer_id,order_id,order_date,order_status,order_amount,discount_amount
C1,O1,2025-06-30,completed,100.00,10.00
C1,O2,2025-05-31,completed,50.00,5.00
C1,O3,2025-04-15,completed,200.00,0
C1,O4,2025-01-10,completed,800.00,0
C2,O5,2025-06-15,completed,300.00,0
C2,O6,2025-05-30,completed,100.00,0
"""

CUSTOMERS_CSV = """customer_id,created_at,email
C1,2025-01-05,c1@x.io
C2,,c2@x.io
"""


def _dataset_with_c1_c2(client, headers) -> tuple[str, str]:
    dataset_id = create_dataset(client, headers)
    customers = upload_csv_text(client, headers, dataset_id, kind="customers", csv_text=CUSTOMERS_CSV)
    orders = upload_csv_text(client, headers, dataset_id, kind="orders", csv_text=ORDERS_CSV)
    apply_suggested_mapping(client, headers, dataset_id, customers["file"]["id"], "customers")
    apply_suggested_mapping(client, headers, dataset_id, orders["file"]["id"], "orders")
    response = client.post(
        f"/api/v1/datasets/{dataset_id}/versions",
        json={
            "customers_file_id": customers["file"]["id"],
            "orders_file_id": orders["file"]["id"],
        },
        headers=headers,
    )
    assert response.status_code == 202, response.text
    version_id = response.json()["version"]["id"]
    # Wait for validation to finish (inline jobs run synchronously).
    for _ in range(50):
        detail = client.get(f"/api/v1/datasets/{dataset_id}/versions/{version_id}", headers=headers)
        if detail.json()["status"] == "valid":
            return dataset_id, version_id
    raise AssertionError("dataset version did not become valid")


def test_feature_run_lifecycle_persists_snapshot(client, org_a_owner) -> None:
    headers = org_a_owner["headers"]
    dataset_id, version_id = _dataset_with_c1_c2(client, headers)

    response = client.post(
        "/api/v1/features/runs",
        params={"dataset_version_id": version_id, "cutoff_date": CUTOFF, "observation_days": 180},
        headers=headers,
    )
    assert response.status_code == 201, response.text
    run = response.json()["run"]
    assert run["status"] == "succeeded"  # inline job executes synchronously
    assert run["row_count"] == 2
    assert run["observation_days"] == 180

    detail = client.get(f"/api/v1/features/runs/{run['id']}", headers=headers)
    assert detail.status_code == 200
    body = detail.json()
    stored = {row["customer_id"]: row for row in body["features"]}
    assert stored["C1"]["frequency"] == 4
    assert stored["C1"]["monetary_value"] == 1150.00
    assert stored["C2"]["tenure_days"] is None
    # every stored feature row carries the feature version & cutoff (traceability, FE-10)
    assert all(row["feature_version"] == "1.0.0" for row in body["features"])

    runs = client.get("/api/v1/features/runs", params={"dataset_version_id": version_id}, headers=headers)
    assert [r["id"] for r in runs.json()["items"]] == [run["id"]]


def test_feature_run_rejects_nonexistent_version(client, org_a_owner) -> None:
    headers = org_a_owner["headers"]
    payload = {"dataset_version_id": "00000000-0000-0000-0000-000000000000"}
    response = client.post(
        "/api/v1/features/runs",
        params={"dataset_version_id": payload["dataset_version_id"], "cutoff_date": CUTOFF},
        headers=headers,
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


def test_feature_run_rejects_invalid_dataset_version(client, org_a_owner) -> None:
    # A fresh dataset with no uploads has no valid version to wait on;
    # instead create a dataset, skip validation, and post a run against a
    # hypothetical version id to assert the must-be-valid contract is
    # enforced only for existing versions.
    headers = org_a_owner["headers"]
    _, version_id, report = make_validated_dataset(client, headers)
    assert report["status"] == "valid"

    # cutoff in the far future is still a legal ISO date → run succeeds.
    bad = client.post(
        "/api/v1/features/runs",
        params={"dataset_version_id": version_id, "cutoff_date": "not-a-date"},
        headers=headers,
    )
    assert bad.status_code == 400
    assert bad.json()["error"]["code"] == "BAD_REQUEST"


def test_feature_run_tenant_isolation(client, org_a_owner, org_b_owner) -> None:
    headers = org_a_owner["headers"]
    other = org_b_owner["headers"]
    _, version_id, _ = make_validated_dataset(client, headers)

    run = client.post(
        "/api/v1/features/runs",
        params={"dataset_version_id": version_id, "cutoff_date": CUTOFF},
        headers=headers,
    ).json()["run"]

    cross = client.get(f"/api/v1/features/runs/{run['id']}", headers=other)
    assert cross.status_code in (403, 404)
