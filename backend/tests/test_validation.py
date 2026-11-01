"""Validation engine unit tests + API report exposure (DATA-09/10, DATA-15, PRD §8.2)."""

from __future__ import annotations

import pytest

from app.domains.datasets.validation import run_validation
from tests.conftest import (
    FIXTURES_DIR,
    apply_suggested_mapping,
    create_dataset,
    make_validated_dataset,
    upload_csv_text,
    upload_fixture,
)

CUSTOMERS_MAP = {"customer_id": "customer_id", "created_at": "created_at"}
ORDERS_MAP = {
    "order_id": "order_id",
    "customer_id": "customer_id",
    "order_date": "order_date",
    "order_status": "order_status",
    "order_amount": "order_amount",
    "currency": "currency",
    "discount_amount": "discount_amount",
    "shipping_amount": "shipping_amount",
    "item_count": "item_count",
}


def _run(orders: str, customers: str | None = None) -> dict:
    customers_bytes = customers.encode() if customers is not None else b"customer_id\nc-1\nc-2\nc-3\n"
    return run_validation(customers_bytes, orders.encode(), CUSTOMERS_MAP, ORDERS_MAP)


# --- engine unit tests --------------------------------------------------------


def test_valid_reports_clean_counts() -> None:
    customers = FIXTURES_DIR.joinpath("valid_customers.csv").read_text()
    orders = FIXTURES_DIR.joinpath("valid_orders.csv").read_text()
    report = run_validation(customers.encode(), orders.encode(), CUSTOMERS_MAP, ORDERS_MAP)
    assert report["status"] == "valid", report["issues"]
    assert report["summary"]["customers"]["rows_total"] == 6
    assert report["summary"]["orders"]["rows_total"] == 14
    assert report["summary"]["orders"]["currency_counts"] == {"USD": 14}
    statuses = report["summary"]["orders"]["status_counts"]
    assert statuses["completed"] == 8
    assert statuses["refunded"] == 1
    assert statuses["cancelled"] == 1
    assert statuses["partially_refunded"] == 1
    assert statuses["pending"] == 1
    assert statuses["processing"] == 1
    assert statuses["return"] == 1
    assert report["summary"]["orders"]["date_min"].startswith("2025-03-01")
    assert report["issue_counts"]["errors"] == 0
    # Missing optional customer status cell (row 3) is a warning, not error.
    assert report["issue_counts"]["warnings"] >= 1


def test_duplicate_ids_flagged_with_row_numbers() -> None:
    orders = (
        "order_id,customer_id,order_date,order_status,order_amount\n"
        "O-1,c-1,2025-05-01 10:00:00,completed,10\n"
        "O-1,c-1,2025-05-02 10:00:00,completed,10\n"
        "O-2,c-1,2025-05-03 10:00:00,completed,10\n"
    )
    report = _run(orders)
    dup = next(i for i in report["issues"] if i["code"] == "DUPLICATE_ID")
    assert dup["count"] == 1
    assert dup["example_rows"] == [2]  # 1-based data row numbers


def test_missing_required_values_flagged() -> None:
    orders = (
        "order_id,customer_id,order_date,order_status,order_amount\n"
        ",c-1,2025-05-01 10:00:00,completed,10\n"
        "O-1,c-1,2025-05-02 10:00:00,completed,\n"
    )
    report = _run(orders)
    by_field = {i["field"]: i["code"] for i in report["issues"] if i["file"] == "orders"}
    assert by_field.get("order_id") == "MISSING_REQUIRED_VALUE"
    assert by_field.get("order_amount") == "MISSING_REQUIRED_VALUE"
    assert report["status"] == "invalid"


def test_mixed_currencies_error() -> None:
    orders = (
        "order_id,customer_id,order_date,order_status,order_amount,currency\n"
        "O-1,c-1,2025-05-01 10:00:00,completed,10,USD\n"
        "O-2,c-1,2025-05-02 10:00:00,completed,10,EUR\n"
    )
    report = _run(orders)
    mixed = next(i for i in report["issues"] if i["code"] == "MIXED_CURRENCIES")
    assert mixed["severity"] == "error"
    assert mixed["count"] == 2  # rows carrying the conflicting currencies


def test_mixed_timezones_error_and_naive_warning() -> None:
    mixed = (
        "order_id,customer_id,order_date,order_status,order_amount\n"
        "O-1,c-1,2025-05-01T10:00:00Z,completed,10\n"
        "O-2,c-1,2025-05-02 10:00:00,completed,10\n"
    )
    report = _run(mixed)
    assert any(i["code"] == "MIXED_TIMEZONES" for i in report["issues"] if i["file"] == "orders")

    naive = (
        "order_id,customer_id,order_date,order_status,order_amount\n"
        "O-1,c-1,2025-05-01 10:00:00,completed,10\n"
        "O-2,c-1,2025-05-02 10:00:00,completed,10\n"
    )
    report = _run(naive)
    naive_issue = next(i for i in report["issues"] if i["code"] == "NAIVE_TIMESTAMPS" and i["file"] == "orders")
    assert naive_issue["severity"] == "warning"


def test_invalid_status_and_fk_violation() -> None:
    orders = (
        "order_id,customer_id,order_date,order_status,order_amount\n"
        "O-1,c-1,2025-05-01 10:00:00,banana,10\n"
        "O-2,c-x,2025-05-02 10:00:00,completed,10\n"
    )
    report = _run(orders)
    codes = {i["code"] for i in report["issues"]}
    assert "INVALID_ORDER_STATUS" in codes
    assert "FOREIGN_KEY_VIOLATION" in codes
    assert report["status"] == "invalid"


def test_amounts_discounts_item_count() -> None:
    orders = (
        "order_id,customer_id,order_date,order_status,order_amount,discount_amount,shipping_amount,item_count\n"
        "O-1,c-1,2025-05-01 10:00:00,completed,10,50,0,1\n"  # discount exceeds (warning)
        "O-2,c-1,2025-05-02 10:00:00,completed,-5,0,0,1\n"  # negative amount (error)
        "O-3,c-1,2025-05-03 10:00:00,completed,10,0,0,1.5\n"  # fractional count (error)
    )
    report = _run(orders)
    codes = {i["code"]: i for i in report["issues"]}
    assert "NEGATIVE_AMOUNT" in codes
    assert codes.get("DISCOUNT_EXCEEDS_AMOUNT", {}).get("severity") == "warning"
    assert "INVALID_NUMBER" in codes
    assert report["status"] == "invalid"


def test_future_date_warning_and_coverage() -> None:
    orders = (
        "order_id,customer_id,order_date,order_status,order_amount\n"
        "O-1,c-1,2025-05-01 10:00:00,completed,10\n"
        "O-2,c-1,2099-01-01 10:00:00,completed,10\n"
    )
    report = _run(orders)
    codes = {i["code"] for i in report["issues"]}
    assert "FUTURE_DATES" in codes
    assert report["summary"]["orders"]["date_max"].startswith("2099-01-01")


def test_missing_currency_is_warning_only() -> None:
    orders = (
        "order_id,customer_id,order_date,order_status,order_amount,currency\n"
        "O-1,c-1,2025-05-01 10:00:00,completed,10,\n"
    )
    report = _run(orders)
    missing = next(i for i in report["issues"] if i["code"] == "MISSING_CURRENCY")
    assert missing["severity"] == "warning"
    assert report["status"] == "valid"


# --- API-level tests ------------------------------------------------------------


@pytest.fixture()
def validated(client, org_a_owner):
    headers = org_a_owner["headers"]
    return make_validated_dataset(client, headers)


def test_version_valid_report_exposed(validated) -> None:
    _, _, body = validated
    assert body["status"] == "valid"
    assert body["report"]["issue_counts"]["errors"] == 0
    assert body["job_id"]


def test_invalid_version_reports_issue_rows(client, org_a_owner) -> None:
    headers = org_a_owner["headers"]
    dataset_id = create_dataset(client, headers)
    customers = upload_fixture(client, headers, dataset_id, kind="customers", filename="valid_customers.csv")
    orders = upload_fixture(client, headers, dataset_id, kind="orders", filename="invalid_orders.csv")
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
    assert response.status_code == 202
    version_id = response.json()["version"]["id"]
    detail = client.get(f"/api/v1/datasets/{dataset_id}/versions/{version_id}", headers=headers).json()
    assert detail["status"] == "invalid"
    codes = {i["code"] for i in detail["report"]["issues"]}
    assert {
        "DUPLICATE_ID",
        "FOREIGN_KEY_VIOLATION",
        "INVALID_ORDER_STATUS",
        "INVALID_DATE",
        "MIXED_CURRENCIES",
        "NEGATIVE_AMOUNT",
        "MIXED_TIMEZONES",
        "INVALID_NUMBER",
    } <= codes
    dup = next(i for i in detail["report"]["issues"] if i["code"] == "DUPLICATE_ID")
    assert dup["example_rows"] == [2]


def test_mapping_against_incomplete_file_is_actionable(client, org_a_owner) -> None:
    """Missing required source columns give a clear, actionable error (PRD §8.5)."""
    headers = org_a_owner["headers"]
    dataset_id = create_dataset(client, headers)
    orders = upload_fixture(client, headers, dataset_id, kind="orders", filename="incomplete_orders.csv")
    response = client.patch(
        f"/api/v1/datasets/{dataset_id}/files/{orders['file']['id']}",
        json={"mapping": ORDERS_MAP},
        headers=headers,
    )
    assert response.status_code == 422
    assert "order_amount" in response.json()["error"]["message"]


def test_failed_version_cannot_overwrite_previous_valid_once(client, org_a_owner) -> None:
    """Version immutability: v1 stays valid even when a later version fails."""
    headers = org_a_owner["headers"]
    dataset_id, version_id, _ = make_validated_dataset(client, headers)

    # A second upload with duplicate order ids must produce an invalid v2,
    # while the *previous* valid version remains untouched.
    empty = upload_csv_text(
        client,
        headers,
        dataset_id,
        kind="orders",
        csv_text=(
            "order_id,customer_id,order_date,order_status,order_amount,currency\n"
            "O-8881,C-1001,2025-05-01 10:00:00,completed,10.00,USD\n"
            "O-8881,C-1001,2025-05-02 10:00:00,completed,10.00,USD\n"
        ),
    )
    apply_suggested_mapping(client, headers, dataset_id, empty["file"]["id"], "orders")

    detail = client.get(f"/api/v1/datasets/{dataset_id}", headers=headers)
    assert detail.status_code == 200
    customers_file_id = detail.json()["files"][0]["id"]

    response = client.post(
        f"/api/v1/datasets/{dataset_id}/versions",
        json={
            "customers_file_id": customers_file_id,
            "orders_file_id": empty["file"]["id"],
        },
        headers=headers,
    )
    assert response.status_code == 202
    v2_id = response.json()["version"]["id"]

    v1 = client.get(f"/api/v1/datasets/{dataset_id}/versions/{version_id}", headers=headers).json()
    assert v1["status"] == "valid"
    assert v1["version_number"] == 1

    v2 = client.get(f"/api/v1/datasets/{dataset_id}/versions/{v2_id}", headers=headers).json()
    assert v2["status"] != "valid"  # degenerate/empty data cannot pass silently
    assert v2["version_number"] == 2
