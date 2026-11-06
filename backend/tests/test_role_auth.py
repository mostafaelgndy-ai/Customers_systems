"""TEST-09: role authorization matrix (AUTH-04).

Registering users are owners. Viewer (member) / analyst / owner permissions
are enforced by `role_checker` at every org-scoped route — this suite pins
the matrix for the endpoints that matter for data safety.
"""

from __future__ import annotations

from sqlalchemy import select

from app.core.config import settings
from app.domains.identity.models import Membership
from tests.conftest import add_organization_member, headers_for, login
from tests.test_training import _csv_dataset


def _first_org_id(app_db) -> str:
    """Org A is the only organization in these tests; resolve it once."""
    _engine, factory = app_db
    with factory() as session:
        membership = session.scalar(select(Membership).limit(1))
        return str(membership.organization_id)


def _role_headers(client, app_db, email: str) -> dict:
    """Insert membership isn't needed here; login existing member user."""
    tokens = login(client, email)
    return {**headers_for(tokens), "X-Organization-ID": _first_org_id(app_db)}


def test_viewer_role_read_only_matrix(client, org_a_owner, app_db, tmp_path) -> None:
    """Members can view dashboards/lists but may not mutate anything."""
    headers = org_a_owner["headers"]
    org_id = _first_org_id(app_db)
    add_organization_member(app_db, organization_id=org_id, email="member@test.io", role="member")
    member_headers = _role_headers(client, app_db, "member@test.io")

    # read: allowed
    assert client.get("/api/v1/datasets", headers=member_headers).status_code == 200
    assert client.get("/api/v1/churn/model-runs", headers=member_headers).status_code == 200
    assert client.get("/api/v1/segmentation/runs", headers=member_headers).status_code == 200
    assert client.get("/api/v1/jobs", headers=member_headers).status_code == 200

    # write: forbidden everywhere
    dataset_id, version_id = _csv_dataset(client, headers)
    checks = {
        "post_dataset": lambda: client.post("/api/v1/datasets", json={"name": "x"}, headers=member_headers),
        "post_version": lambda: client.post(
            f"/api/v1/datasets/{dataset_id}/versions",
            json={"customers_file_id": "a", "orders_file_id": "b"},
            headers=member_headers,
        ),
        "post_churn_run": lambda: client.post(
            "/api/v1/churn/model-runs", json={"dataset_version_id": version_id}, headers=member_headers
        ),
        "post_segmentation_run": lambda: client.post(
            "/api/v1/segmentation/runs", json={"dataset_version_id": version_id}, headers=member_headers
        ),
        "post_export": lambda: client.post(
            "/api/v1/exports", json={"kind": "churn_scores", "run_id": version_id}, headers=member_headers
        ),
        "delete_dataset": lambda: client.delete(f"/api/v1/datasets/{dataset_id}", headers=member_headers),
    }
    for name, call in checks.items():
        response = call()
        assert response.status_code == 403, f"{name}: expected 403, got {response.status_code} {response.text[:120]}"


def test_member_cannot_upload_into_org_dataset(client, org_a_owner, app_db) -> None:
    headers = org_a_owner["headers"]
    org_id = _first_org_id(app_db)
    add_organization_member(app_db, organization_id=org_id, email="member2@test.io", role="member")
    member_headers = _role_headers(client, app_db, "member2@test.io")
    dataset_id, _ = _csv_dataset(client, headers)

    denied = client.post(
        f"/api/v1/datasets/{dataset_id}/files",
        data={"kind": "customers"},
        files={"file": ("c.csv", "customer_id\nC1\n", "text/csv")},
        headers=member_headers,
    )
    assert denied.status_code == 403


def test_analyst_can_analyze_but_not_delete(client, org_a_owner, app_db, tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(settings, "models_dir", tmp_path / "models", raising=False)
    org_id = _first_org_id(app_db)
    add_organization_member(app_db, organization_id=org_id, email="analyst@test.io", role="analyst")
    analyst_headers = _role_headers(client, app_db, "analyst@test.io")

    # analyst can produce analysis on shared org data
    created = client.post("/api/v1/datasets", json={"name": "Analyst make"}, headers=analyst_headers)
    assert created.status_code == 201, created.text
    dataset_id, version_id = _csv_dataset(client, analyst_headers)

    run = client.post("/api/v1/churn/model-runs", json={"dataset_version_id": version_id}, headers=analyst_headers)
    assert run.status_code == 202, run.text
    run_id = run.json()["run"]["id"]
    scored = client.post("/api/v1/churn/scores", json={"model_run_id": run_id}, headers=analyst_headers)
    assert scored.status_code == 202, scored.text
    export = client.post("/api/v1/exports", json={"kind": "churn_scores", "run_id": run_id}, headers=analyst_headers)
    assert export.status_code == 202, export.text

    # …but deletion stays owner/admin (TEST-16)
    denied = client.delete(f"/api/v1/datasets/{dataset_id}", headers=analyst_headers)
    assert denied.status_code == 403


def test_owner_cannot_be_seen_by_other_orgs(client, org_a_owner, org_b_owner) -> None:
    """Scope sanity beyond roles: every artifact list of B excludes A's data."""
    dataset_id, _ = _csv_dataset(client, org_a_owner["headers"])
    b_list = client.get("/api/v1/datasets", headers=org_b_owner["headers"]).json()
    assert all(str(d["id"]) != dataset_id for d in b_list["items"])
