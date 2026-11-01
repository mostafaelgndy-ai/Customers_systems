"""CHURN-01..04 acceptance tests: label generation, eligibility, boundaries, distribution."""

from __future__ import annotations

import pandas as pd

from app.domains.churn.labeling import (
    LABEL_ACTIVE,
    LABEL_CHURNED,
    LABEL_INELIGIBLE,
    LabelConfig,
    generate_labels,
    label_distribution,
)

# cutoff = 2025-06-30, observation window 180d → obs start = 2024-12-31
# (inclusive), label horizon 90d → horizon end = 2025-09-28 (inclusive).
CONFIG = LabelConfig(cutoff_date="2025-06-30")

CUSTOMERS = pd.DataFrame(
    {
        "customer_id": [*"ABCDEFG"],
        "email": [f"{c}@x.io" for c in "ABCDEFG"],
    }
)


def _orders(rows: list[tuple[str, str, str, str]]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "customer_id": [r[0] for r in rows],
            "order_id": [f"O{i}" for i in range(1, len(rows) + 1)],
            "order_date": [r[1] for r in rows],
            "order_status": [r[2] for r in rows],
        }
    )


def _label_map(orders: pd.DataFrame, config: LabelConfig = CONFIG) -> dict[str, str]:
    labels = generate_labels(CUSTOMERS, orders, config)
    return {row["customer_id"]: row["label"] for row in labels}


def test_active_when_purchases_resume_within_horizon() -> None:
    orders = _orders(
        [
            ("A", "2025-01-10", "completed"),  # observed
            ("A", "2025-07-20", "completed"),  # future purchase inside horizon
        ]
    )
    labels = _label_map(orders)
    assert labels["A"] == LABEL_ACTIVE


def test_churned_when_no_future_purchases_in_horizon() -> None:
    orders = _orders([("B", "2025-01-10", "completed")])
    labels = _label_map(orders)
    assert labels["B"] == LABEL_CHURNED


def test_unknown_future_without_observation_window_is_ineligible() -> None:
    # C only has an order after the cutoff → not attributable to the
    # observation window; excluded, never mislabeled (CHURN-02).
    orders = _orders(
        [
            ("C", "2025-07-20", "completed"),
            (
                "C",
                "2026-01-01",
                "completed",
            ),  # beyond horizon, must be irrelevant anyway
        ]
    )
    labels = _label_map(orders)
    assert labels["C"] == LABEL_INELIGIBLE


def test_boundaries_are_inclusive() -> None:
    orders = _orders(
        [
            # D: order exactly at cutoff → belongs to observation window;
            #    first day of horizon → within horizon.
            ("D", "2025-06-30", "completed"),
            ("D", "2025-07-01", "completed"),
            # E: observed order + order exactly at horizon end → active.
            ("E", "2025-06-29", "completed"),
            ("E", "2025-09-28", "completed"),
            # F: observed order but future order falls one day after the
            #    horizon end → no horizon purchases → churned.
            ("F", "2025-01-10", "completed"),
            ("F", "2025-09-29", "completed"),
        ]
    )
    labels = _label_map(orders)
    assert labels["D"] == LABEL_ACTIVE
    assert labels["E"] == LABEL_ACTIVE
    assert labels["F"] == LABEL_CHURNED


def test_refunds_never_drive_labels() -> None:
    orders = _orders(
        [
            ("G", "2025-03-01", "completed"),
            ("G", "2025-07-20", "refunded"),  # future, but not an eligible purchase
        ]
    )
    labels = _label_map(orders)
    assert labels["G"] == LABEL_CHURNED


def test_repeat_purchases_in_horizon_count_as_active() -> None:
    orders = _orders(
        [
            ("A", "2025-01-10", "completed"),
            ("A", "2025-07-05", "completed"),
            ("A", "2025-08-15", "completed"),  # repeat purchase in horizon
        ]
    )
    labels = _label_map(orders)
    assert labels["A"] == LABEL_ACTIVE


def test_label_config_is_stored_in_labels() -> None:
    orders = _orders([("A", "2025-01-10", "completed")])
    labels = generate_labels(CUSTOMERS, orders, CONFIG)
    assert len(labels) == len(CUSTOMERS)  # one row per customer
    row = next(r for r in labels if r["customer_id"] == "A")
    assert row["observation_window_days"] == 180
    assert row["label_horizon_days"] == 90
    assert row["cutoff_date"] == "2025-06-30"
    assert row["days_since_last_order"] == 171  # hand-calculated: Jan 10 → Jun 30
    assert row["reason"] is None
    other = next(r for r in labels if r["customer_id"] == "F")
    assert other["label"] == LABEL_INELIGIBLE
    assert other["reason"]


def test_configurable_windows_change_boundaries() -> None:
    """CHURN-01: windows are configurable, defaults are 180/90 (experimental)."""
    config = LabelConfig(cutoff_date="2025-06-30", observation_window_days=30, label_horizon_days=45)
    orders = _orders([("A", "2025-06-01", "completed"), ("A", "2025-08-01", "completed")])
    labels = generate_labels(CUSTOMERS, orders, config)
    assert {row["customer_id"]: row["label"] for row in labels}["A"] == LABEL_ACTIVE
    assert labels[0]["observation_window_days"] == 30
    assert labels[0]["label_horizon_days"] == 45
    # 2025-06-01 is inside the 30-day obs window; 2025-08-01 is inside the horizon.
    assert config.observation_start() == "2025-05-31"
    assert config.horizon_end() == "2025-08-14"


def test_label_distribution_reports_counts_and_rates() -> None:
    """CHURN-04: class distribution visible before model training."""
    orders = _orders(
        [
            ("A", "2025-01-10", "completed"),  # churned
            ("A", "2025-07-20", "completed"),  # → active
            ("B", "2025-01-10", "completed"),  # churned
            ("C", "2028-01-01", "completed"),  # ineligible (no obs orders)
        ]
    )
    labels = generate_labels(CUSTOMERS, orders, CONFIG)
    dist = label_distribution(labels)
    assert dist["total_customers"] == 7
    assert dist["churned"] == 1
    assert dist["active"] == 1
    assert dist["ineligible"] == 5
    assert dist["labeled_count"] == 2
    assert dist["churn_rate"] == 0.5
    assert dist["churn_rate_percent"] == 50.0


def test_empty_label_set_distribution() -> None:
    dist = label_distribution([])
    assert dist["labeled_count"] == 0
    assert dist["churn_rate"] is None


# --- API-level tests: churn label endpoints ---------------------------------

LABELS_CSV = """customer_id,order_id,order_date,order_status,order_amount
A,O1,2025-01-10,completed,100.00
A,O2,2025-07-20,completed,50.00
B,O3,2025-01-10,completed,30.00
"""

CUSTOMERS_CSV = """customer_id,created_at,email
A,2024-11-01,a@x.io
B,2024-11-01,b@x.io
"""


def _churn_dataset(client, headers) -> tuple[str, str]:
    from tests.conftest import apply_suggested_mapping, create_dataset, upload_csv_text

    dataset_id = create_dataset(client, headers)
    customers = upload_csv_text(client, headers, dataset_id, kind="customers", csv_text=CUSTOMERS_CSV)
    orders = upload_csv_text(client, headers, dataset_id, kind="orders", csv_text=LABELS_CSV)
    apply_suggested_mapping(client, headers, dataset_id, customers["file"]["id"], "customers")
    apply_suggested_mapping(client, headers, dataset_id, orders["file"]["id"], "orders")
    response = client.post(
        f"/api/v1/datasets/{dataset_id}/versions",
        json={"customers_file_id": customers["file"]["id"], "orders_file_id": orders["file"]["id"]},
        headers=headers,
    )
    assert response.status_code == 202, response.text
    return dataset_id, response.json()["version"]["id"]


def test_labels_endpoint_persists_and_reports_distribution(client, org_a_owner) -> None:
    headers = org_a_owner["headers"]
    dataset_id, version_id = _churn_dataset(client, headers)

    response = client.post(
        "/api/v1/churn/labels",
        json={
            "dataset_id": dataset_id,
            "dataset_version_id": version_id,
            "cutoff_date": "2025-06-30",
        },
        headers=headers,
    )
    assert response.status_code == 201, response.text
    dist = response.json()["distribution"]
    assert dist["total_customers"] == 2
    assert dist["active"] == 1
    assert dist["churned"] == 1
    assert dist["cutoff_date"] == "2025-06-30"

    # stored distribution endpoint agrees (CHURN-04: visible before training)
    stored = client.get(
        "/api/v1/churn/distribution",
        params={"dataset_version_id": version_id},
        headers=headers,
    )
    assert stored.status_code == 200
    assert stored.json()["distribution"]["labeled_count"] == 2

    labels = client.get("/api/v1/churn/labels", params={"dataset_version_id": version_id}, headers=headers)
    assert labels.status_code == 200
    items = labels.json()["items"]
    assert len(items) == 2
    by_cust = {row["customer_id"]: row for row in items}
    assert by_cust["A"]["label"] == "active"
    assert by_cust["B"]["label"] == "churned"
    assert by_cust["A"]["observation_window_days"] == 180


def test_labels_tenant_isolation(client, org_a_owner, org_b_owner) -> None:
    headers = org_a_owner["headers"]
    other = org_b_owner["headers"]
    dataset_id, version_id = _churn_dataset(client, headers)
    client.post(
        "/api/v1/churn/labels",
        json={"dataset_id": dataset_id, "dataset_version_id": version_id, "cutoff_date": "2025-06-30"},
        headers=headers,
    )
    cross = client.get("/api/v1/churn/distribution", params={"dataset_version_id": version_id}, headers=other)
    assert cross.status_code in (403, 404)


def test_labels_schema_endpoint(client, org_a_owner) -> None:
    response = client.get("/api/v1/churn/schema")
    assert response.status_code == 200
    assert "label" in response.json()["schema"]
