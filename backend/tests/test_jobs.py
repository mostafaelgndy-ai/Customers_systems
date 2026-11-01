"""Job lifecycle tests: cancel (queued) and retry (failed) — DATA-13, JOB-08/09."""

from __future__ import annotations

import pytest

from app.core.config import settings
from tests.conftest import (
    apply_suggested_mapping,
    create_dataset,
    upload_csv_text,
    upload_fixture,
)


@pytest.fixture()
def _queued_mode(monkeypatch):
    """Force queue mode but intercept Celery dispatch (no broker in tests)."""
    settings.inline_jobs = False
    captured: list[str] = []
    monkeypatch.setattr(
        "app.workers.tasks.dispatch_job",
        type("T", (), {"delay": staticmethod(lambda job_id: captured.append(job_id))}),
    )
    yield captured
    settings.inline_jobs = True


def test_cancel_queued_validation(client, org_a_owner, monkeypatch, _queued_mode) -> None:
    headers = org_a_owner["headers"]
    dataset_id = create_dataset(client, headers)
    customers = upload_fixture(client, headers, dataset_id, kind="customers", filename="valid_customers.csv")
    orders = upload_fixture(client, headers, dataset_id, kind="orders", filename="valid_orders.csv")
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
    assert _queued_mode  # job was dispatched to the (mocked) queue

    cancel = client.post(f"/api/v1/datasets/{dataset_id}/versions/{version_id}/cancel", headers=headers)
    assert cancel.status_code == 200
    assert cancel.json()["version"]["status"] == "cancelled"


def test_cancel_fails_when_already_final(client, org_a_owner) -> None:
    headers = org_a_owner["headers"]
    dataset_id = create_dataset(client, headers)
    customers = upload_fixture(client, headers, dataset_id, kind="customers", filename="valid_customers.csv")
    orders = upload_fixture(client, headers, dataset_id, kind="orders", filename="valid_orders.csv")
    apply_suggested_mapping(client, headers, dataset_id, customers["file"]["id"], "customers")
    apply_suggested_mapping(client, headers, dataset_id, orders["file"]["id"], "orders")
    version_id = client.post(
        f"/api/v1/datasets/{dataset_id}/versions",
        json={
            "customers_file_id": customers["file"]["id"],
            "orders_file_id": orders["file"]["id"],
        },
        headers=headers,
    ).json()["version"]["id"]
    # Inline mode already finished the job; cancel must refuse.
    cancel = client.post(f"/api/v1/datasets/{dataset_id}/versions/{version_id}/cancel", headers=headers)
    assert cancel.status_code == 409


def test_retry_failed_version_inline(client, org_a_owner, app_db) -> None:
    headers = org_a_owner["headers"]
    dataset_id = create_dataset(client, headers)
    customers = upload_fixture(client, headers, dataset_id, kind="customers", filename="valid_customers.csv")
    empty = upload_csv_text(
        client,
        headers,
        dataset_id,
        kind="orders",
        csv_text="order_id,customer_id,order_date,order_status,order_amount,currency\n",
    )
    apply_suggested_mapping(client, headers, dataset_id, customers["file"]["id"], "customers")
    apply_suggested_mapping(client, headers, dataset_id, empty["file"]["id"], "orders")
    version_id = client.post(
        f"/api/v1/datasets/{dataset_id}/versions",
        json={
            "customers_file_id": customers["file"]["id"],
            "orders_file_id": empty["file"]["id"],
        },
        headers=headers,
    ).json()["version"]["id"]

    # Simulate an engine failure by marking the version failed but leaving the
    # stored files intact; retry must re-run validation and reach `valid`
    # for the same immutable version row.
    engine, factory = app_db
    db = factory()
    from app.domains.datasets.models import DatasetVersion

    version = db.get(DatasetVersion, __import__("uuid").UUID(version_id))
    assert version is not None
    version.status = "failed"
    db.commit()
    db.close()

    retry = client.post(f"/api/v1/datasets/{dataset_id}/versions/{version_id}/retry", headers=headers)
    assert retry.status_code == 200, retry.text
    assert retry.json()["version"]["status"] == "valid"
