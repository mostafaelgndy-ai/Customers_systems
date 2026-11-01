"""FE-01 acceptance tests: EDA report endpoint for a dataset version."""

from __future__ import annotations

import pandas as pd

from app.domains.datasets import eda
from tests.conftest import make_validated_dataset, random_uuid


def test_eda_report_contains_required_sections() -> None:
    """Report includes row counts, date coverage, uniques, missingness,
    duplicates, statuses, and monetary distributions — recomputed from data."""
    customers = pd.DataFrame(
        {
            "customer_id": ["C1", "C2", "C2", "C3"],
            "created_at": ["2025-01-01", "", "2025-01-01", "2025-02-01"],
            "email": ["a@x.io", "", "b@x.io", "c@x.io"],
        }
    )
    orders = pd.DataFrame(
        {
            "customer_id": ["C1", "C1", "C2", "C2", "C2"],
            "order_id": ["O1", "O2", "O3", "O3", "O4"],
            "order_date": ["2025-01-05", "2025-03-10", "", "2025-08-08", "2025-09-09"],
            "order_status": ["completed", "refunded", "completed", "cancelled", "completed"],
            "order_amount": ["100.50", "20.00", "300.00", "400.00", "50.00"],
        }
    )
    report = eda.compute_eda(customers, orders)

    assert report["row_counts"] == {"customers": 4, "orders": 5}

    # unique customers in orders: C1, C2 (2); order ids: O1..O4 (O3 repeats)
    assert report["unique_counts"] == {
        "customers_registered": 3,
        "customers_with_orders": 2,
        "order_ids": 4,
    }
    # duplicated C2 ignored via blank created_at; blank order date counts as missing
    assert report["missing_values"]["customers"] == {"created_at": 1}
    assert report["missing_values"]["orders"] == {"order_date": 1}

    assert report["duplicates"]["orders"]["exact_duplicate_rows"] == 0
    assert report["duplicates"]["orders"]["ids"] == ["O3"]

    assert report["order_status_counts"] == {
        "completed": 3,
        "refunded": 1,
        "cancelled": 1,
    }

    money = report["monetary_distribution"]
    assert money["count"] == 5
    assert money["min"] == 20.00
    assert money["max"] == 400.00
    assert money["sum"] == 870.50

    assert report["date_coverage"]["first_order_date"] == "2025-01-05"
    assert report["date_coverage"]["last_order_date"] == "2025-09-09"


def test_eda_endpoint_returns_report_and_is_scoped(client, org_a_owner) -> None:
    headers = org_a_owner["headers"]
    dataset_id, version_id, detail = make_validated_dataset(client, headers)

    response = client.get(f"/api/v1/datasets/{dataset_id}/versions/{version_id}/eda", headers=headers)
    assert response.status_code == 200, response.text
    body = response.json()["eda"]
    assert body["dataset_version_id"] == version_id
    for section in (
        "row_counts",
        "unique_counts",
        "date_coverage",
        "missing_values",
        "duplicates",
        "order_status_counts",
        "monetary_distribution",
    ):
        assert section in body


def test_eda_endpoint_unknown_version_is_404(client, org_a_owner) -> None:
    headers = org_a_owner["headers"]
    dataset_id, _, _ = make_validated_dataset(client, headers)
    response = client.get(f"/api/v1/datasets/{dataset_id}/versions/{random_uuid()}/eda", headers=headers)
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


def test_eda_endpoint_tenant_isolation(client, org_a_owner, org_b_owner) -> None:
    headers = org_a_owner["headers"]
    other = org_b_owner["headers"]
    dataset_id, version_id, _ = make_validated_dataset(client, headers)
    response = client.get(f"/api/v1/datasets/{dataset_id}/versions/{version_id}/eda", headers=other)
    assert response.status_code in (403, 404)
