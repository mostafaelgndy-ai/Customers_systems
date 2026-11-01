"""Shared pytest fixtures: per-test SQLite DB, app client, auth helpers (DATA-14/16)."""

from __future__ import annotations

import os
import pathlib
import uuid

_root = pathlib.Path(__file__).resolve().parents[1]
os.environ.setdefault("SECRET_KEY", "test-secret-key-not-for-production")
os.environ.setdefault("ENVIRONMENT", "dev")
os.environ.setdefault("INLINE_JOBS", "true")

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.config import settings
from app.db.base import Base
from app.db.session import get_db
from app.main import create_app

FIXTURES_DIR = pathlib.Path(__file__).parent / "fixtures"


# Ensure all model modules are imported before create_all.
from app.domains.churn import models as _churn_models  # noqa: E402,F401
from app.domains.datasets import models as _datasets_models  # noqa: E402,F401
from app.domains.features import models as _features_models  # noqa: E402,F401
from app.domains.identity import models as _identity_models  # noqa: E402,F401
from app.domains.jobs import models as _jobs_models  # noqa: E402,F401


@pytest.fixture()
def app_db(tmp_path: pathlib.Path):
    db_path = tmp_path / "test.db"
    engine = create_engine(f"sqlite:///{db_path.as_posix()}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    settings.upload_dir = tmp_path / "uploads"
    settings.inline_jobs = True
    return engine, factory


@pytest.fixture()
def client(app_db):
    engine, factory = app_db
    app = create_app()

    def override_get_db():
        db = factory()
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
    engine.dispose()


# --- auth helpers ---------------------------------------------------------


def register(client: TestClient, *, email: str, org: str) -> dict:
    response = client.post(
        "/api/v1/auth/register",
        json={
            "email": email,
            "password": "hunter2hunter2",
            "full_name": "Test User",
            "organization_name": org,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def login(client: TestClient, email: str) -> dict:
    response = client.post("/api/v1/auth/login", json={"email": email, "password": "hunter2hunter2"})
    assert response.status_code == 200, response.text
    return response.json()


def headers_for(tokens: dict) -> dict[str, str]:
    return {"Authorization": f"Bearer {tokens['access_token']}"}


@pytest.fixture()
def org_a_owner(client) -> dict:
    tokens = register(client, email="owner-a@test.io", org="Org A")
    return {"tokens": tokens, "headers": headers_for(tokens)}


@pytest.fixture()
def org_b_owner(client) -> dict:
    tokens = register(client, email="owner-b@test.io", org="Org B")
    return {"tokens": tokens, "headers": headers_for(tokens)}


# --- dataset helpers ------------------------------------------------------


def create_dataset(client: TestClient, headers: dict, name: str = "Pilot") -> str:
    response = client.post("/api/v1/datasets", json={"name": name}, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()["dataset"]["id"]


def upload_fixture(client: TestClient, headers: dict, dataset_id: str, *, kind: str, filename: str) -> dict:
    path = FIXTURES_DIR / filename
    response = client.post(
        f"/api/v1/datasets/{dataset_id}/files",
        data={"kind": kind},
        files={"file": (filename, path.read_text(encoding="utf-8"), "text/csv")},
        headers=headers,
    )
    assert response.status_code == 201, response.text
    return response.json()


def upload_csv_text(client: TestClient, headers: dict, dataset_id: str, *, kind: str, csv_text: str) -> dict:
    response = client.post(
        f"/api/v1/datasets/{dataset_id}/files",
        data={"kind": kind},
        files={"file": (f"{kind}.csv", csv_text, "text/csv")},
        headers=headers,
    )
    assert response.status_code == 201, response.text
    return response.json()


def apply_suggested_mapping(client: TestClient, headers: dict, dataset_id: str, file_id: str, kind: str) -> dict:
    """PATCH the file with its suggested mapping (suggestions set all fields)."""
    detail = client.get(f"/api/v1/datasets/{dataset_id}/files/{file_id}", headers=headers).json()
    suggested = detail["suggested_mapping"]
    mapping = {k: v for k, v in suggested.items() if v}
    response = client.patch(
        f"/api/v1/datasets/{dataset_id}/files/{file_id}",
        json={"mapping": mapping},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    return response.json()


def make_validated_dataset(
    client: TestClient,
    headers: dict,
    *,
    customers_file: str = "valid_customers.csv",
    orders_file: str = "valid_orders.csv",
) -> tuple[str, str, dict]:
    """End-to-end helper: dataset → uploads → mappings → version → report."""
    dataset_id = create_dataset(client, headers)
    customers = upload_fixture(client, headers, dataset_id, kind="customers", filename=customers_file)
    orders = upload_fixture(client, headers, dataset_id, kind="orders", filename=orders_file)
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

    detail = client.get(f"/api/v1/datasets/{dataset_id}/versions/{version_id}", headers=headers)
    assert detail.status_code == 200, detail.text
    return dataset_id, version_id, detail.json()


def random_uuid() -> str:
    return str(uuid.uuid4())
