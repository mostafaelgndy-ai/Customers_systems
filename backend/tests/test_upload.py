"""Upload, mapping, templates (API-03/04, DATA-05/07/08)."""

from __future__ import annotations

import pathlib

from tests.conftest import (
    apply_suggested_mapping,
    create_dataset,
    upload_csv_text,
    upload_fixture,
)

TEMPLATES_DIR = pathlib.Path(__file__).resolve().parents[1] / "templates"


def test_upload_returns_suggested_mapping(client, org_a_owner) -> None:
    headers = org_a_owner["headers"]
    dataset_id = create_dataset(client, headers)
    result = upload_fixture(client, headers, dataset_id, kind="orders", filename="orders_alias.csv")
    mapping = result["suggested_mapping"]
    assert mapping["order_id"] == "Order ID"
    assert mapping["order_amount"] == "Grand Total"
    assert mapping["order_status"] == "Status"
    assert result["file"]["status"] == "uploaded"
    assert result["file"]["sha256"]


def test_mapping_patch_and_validation(client, org_a_owner) -> None:
    headers = org_a_owner["headers"]
    dataset_id = create_dataset(client, headers)
    upload = upload_csv_text(
        client,
        headers,
        dataset_id,
        kind="customers",
        csv_text="Cust ID,Joined\nC-9,2025-01-01\n",
    )
    file_id = upload["file"]["id"]
    assert upload["suggested_mapping"]["customer_id"] == "Cust ID"

    response = client.patch(
        f"/api/v1/datasets/{dataset_id}/files/{file_id}",
        json={"mapping": {"customer_id": "Cust ID", "created_at": "Joined"}},
        headers=headers,
    )
    assert response.status_code == 200
    assert response.json()["file"]["status"] == "mapped"
    assert response.json()["ready_for_version"] is True


def test_mapping_unknown_column_rejected(client, org_a_owner) -> None:
    headers = org_a_owner["headers"]
    dataset_id = create_dataset(client, headers)
    upload = upload_csv_text(client, headers, dataset_id, kind="customers", csv_text="customer_id\nC-1\n")
    file_id = upload["file"]["id"]
    response = client.patch(
        f"/api/v1/datasets/{dataset_id}/files/{file_id}",
        json={"mapping": {"customer_id": "no_such_column"}},
        headers=headers,
    )
    assert response.status_code == 422
    assert "no_such_column" in response.json()["error"]["message"]

    # Unknown canonical target
    response = client.patch(
        f"/api/v1/datasets/{dataset_id}/files/{file_id}",
        json={"mapping": {"not_a_field": "customer_id"}},
        headers=headers,
    )
    assert response.status_code == 422


def test_mapping_requires_fields(client, org_a_owner) -> None:
    headers = org_a_owner["headers"]
    dataset_id = create_dataset(client, headers)
    upload = upload_csv_text(client, headers, dataset_id, kind="orders", csv_text="order_id\nO-1\n")
    file_id = upload["file"]["id"]
    response = client.patch(
        f"/api/v1/datasets/{dataset_id}/files/{file_id}",
        json={"mapping": {"order_id": "order_id"}},
        headers=headers,
    )
    assert response.status_code == 422
    assert "required canonical" in response.json()["error"]["message"]


def test_binary_upload_rejected(client, org_a_owner) -> None:
    headers = org_a_owner["headers"]
    dataset_id = create_dataset(client, headers)
    response = client.post(
        f"/api/v1/datasets/{dataset_id}/files",
        data={"kind": "customers"},
        files={"file": ("fake.csv", b"PK\x03\x04binarydata", "text/csv")},
        headers=headers,
    )
    assert response.status_code == 415
    assert response.json()["error"]["code"] == "UNSUPPORTED_MEDIA_TYPE"


def test_bad_extension_rejected(client, org_a_owner) -> None:
    headers = org_a_owner["headers"]
    dataset_id = create_dataset(client, headers)
    response = client.post(
        f"/api/v1/datasets/{dataset_id}/files",
        data={"kind": "customers"},
        files={"file": ("payload.docx", b"hello,world\n1,2\n", "text/csv")},
        headers=headers,
    )
    assert response.status_code == 415


def test_upload_size_limit_enforced(client, org_a_owner, monkeypatch) -> None:
    headers = org_a_owner["headers"]
    dataset_id = create_dataset(client, headers)
    monkeypatch.setattr(type(client), "_pass", None, raising=False)
    from app.core.config import settings as app_settings

    original = app_settings.max_upload_mb
    monkeypatch.setattr(app_settings, "max_upload_mb", 0)
    response = client.post(
        f"/api/v1/datasets/{dataset_id}/files",
        data={"kind": "customers"},
        files={"file": ("big.csv", b"customer_id\nC-1\n", "text/csv")},
        headers=headers,
    )
    monkeypatch.setattr(app_settings, "max_upload_mb", original)
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "PAYLOAD_TOO_LARGE"


def test_templates_download_and_pass_validator(client, org_a_owner) -> None:
    headers = org_a_owner["headers"]
    for kind in ("customers", "orders"):
        response = client.get(f"/api/v1/datasets/templates/{kind}")
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/csv")

        # Template files must themselves pass the validator (DATA-05).
        text = TEMPLATES_DIR.joinpath(f"{kind}_template.csv").read_text(encoding="utf-8")
        dataset_id = create_dataset(client, headers)
        customers = upload_csv_text(
            client,
            headers,
            dataset_id,
            kind="customers",
            csv_text=(
                TEMPLATES_DIR.joinpath("customers_template.csv").read_text(encoding="utf-8")
                if kind == "orders"
                else text
            ),
        )
        orders = upload_csv_text(
            client,
            headers,
            dataset_id,
            kind="orders",
            csv_text=(
                TEMPLATES_DIR.joinpath("orders_template.csv").read_text(encoding="utf-8")
                if kind == "orders"
                else TEMPLATES_DIR.joinpath("orders_template.csv").read_text(encoding="utf-8")
            ),
        )
        apply_suggested_mapping(client, headers, dataset_id, customers["file"]["id"], "customers")
        apply_suggested_mapping(client, headers, dataset_id, orders["file"]["id"], "orders")
        version = client.post(
            f"/api/v1/datasets/{dataset_id}/versions",
            json={
                "customers_file_id": customers["file"]["id"],
                "orders_file_id": orders["file"]["id"],
            },
            headers=headers,
        )
        detail = client.get(
            f"/api/v1/datasets/{dataset_id}/versions/{version.json()['version']['id']}",
            headers=headers,
        )
        assert detail.json()["status"] == "valid", detail.text
