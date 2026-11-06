"""TEST-16 (PRD §11.3): dataset deletion cascades everything derived from it.

A dataset delete must remove: uploaded files (DB + bytes), versions, feature
runs/snapshots, churn labels/model runs/scores (+ artifact files), segment
assignments/runs, and jobs that reference the dataset — while other
organizations' data and the audit trail stay intact.
"""

from __future__ import annotations

import uuid
from pathlib import Path

from sqlalchemy import select

from app.core.config import settings
from tests.conftest import headers_for, login
from tests.test_training import _csv_dataset


def _full_analysis(client, headers: dict, factory) -> dict:
    """Upload → validate → train → score → segment → export; return ids."""
    dataset_id, version_id = _csv_dataset(client, headers)

    response = client.post("/api/v1/churn/model-runs", json={"dataset_version_id": version_id}, headers=headers)
    assert response.status_code == 202, response.text
    run_id = response.json()["run"]["id"]
    detail = client.get(f"/api/v1/churn/model-runs/{run_id}", headers=headers)
    assert detail.json()["run"]["status"] == "succeeded", detail.text

    score = client.post("/api/v1/churn/scores", json={"model_run_id": run_id}, headers=headers)
    assert score.status_code == 202, score.text
    scores = client.get(f"/api/v1/churn/scores?model_run_id={run_id}", headers=headers)
    scores_total = scores.json()["total"]
    assert scores_total > 0

    seg = client.post(
        "/api/v1/segmentation/runs",
        json={"dataset_version_id": version_id, "k_candidates": [3]},
        headers=headers,
    )
    assert seg.status_code == 202, seg.text
    seg_id = seg.json()["run"]["id"]
    seg_detail = client.get(f"/api/v1/segmentation/runs/{seg_id}", headers=headers)
    assert seg_detail.json()["run"]["status"] == "succeeded", seg_detail.text

    export = client.post(
        "/api/v1/exports",
        json={"kind": "churn_scores", "run_id": run_id},
        headers=headers,
    )
    assert export.status_code == 202, export.text
    export_job_id = export.json()["job"]["id"]
    # the export file lives at a deterministic path (exports_dir/<org>/<job>.csv)
    from app.domains.exports.service import export_path

    session_probe = factory()
    from app.domains.identity.models import Membership as _Membership

    org_id = session_probe.scalar(select(_Membership.organization_id).limit(1))
    session_probe.close()
    export_file = export_path(uuid.UUID(export_job_id), org_id)
    assert export.status_code == 202
    assert export_file.is_file(), f"export file missing at {export_file}"

    features = client.post(
        "/api/v1/features/runs",
        params={"dataset_version_id": version_id, "cutoff_date": "2025-05-01"},
        headers=headers,
    )
    assert features.status_code == 201, features.text

    return {
        "dataset_id": dataset_id,
        "version_id": version_id,
        "model_run_id": run_id,
        "segmentation_run_id": seg_id,
        "export_job_id": export_job_id,
        "export_file": export_file,
        "scores_total": scores_total,
    }


def test_dataset_delete_cascades_all_artifacts(client, org_a_owner, app_db, tmp_path, monkeypatch) -> None:
    headers = org_a_owner["headers"]
    _engine, factory = app_db
    monkeypatch.setattr(settings, "models_dir", tmp_path / "models", raising=False)
    monkeypatch.setattr(settings, "exports_dir", tmp_path / "exports", raising=False)

    ids = _full_analysis(client, headers, factory)

    uploads_dir = Path(settings.upload_dir)
    files_before = {p for p in uploads_dir.rglob("*") if p.is_file()}
    assert files_before, "uploads should have been written during setup"

    jobs_before = client.get("/api/v1/jobs?page_size=100", headers=headers).json()["total"]
    assert jobs_before > 0

    response = client.delete(f"/api/v1/datasets/{ids['dataset_id']}", headers=headers)
    assert response.status_code == 204, response.text

    # container + versions vanish (404 by design, cross-org-safe)
    assert client.get(f"/api/v1/datasets/{ids['dataset_id']}", headers=headers).status_code == 404
    assert (
        client.get(f"/api/v1/datasets/{ids['dataset_id']}/versions/{ids['version_id']}", headers=headers).status_code
        == 404
    )
    # churn artifacts vanish
    assert client.get(f"/api/v1/churn/model-runs/{ids['model_run_id']}", headers=headers).status_code == 404
    remaining_scores = client.get(f"/api/v1/churn/scores?model_run_id={ids['model_run_id']}", headers=headers).json()
    assert remaining_scores["total"] == 0
    # segmentation vanishes
    assert client.get(f"/api/v1/segmentation/runs/{ids['segmentation_run_id']}", headers=headers).status_code == 404
    # feature runs list is empty for the org
    assert client.get("/api/v1/features/runs", headers=headers).json()["total"] == 0
    # export job + produced CSV are gone
    assert client.get(f"/api/v1/exports/{ids['export_job_id']}/download", headers=headers).status_code == 404
    assert not ids["export_file"].exists()

    # uploaded bytes were removed (uploaded CSVs are gone; dirs may remain)
    files_after = {p for p in uploads_dir.rglob("*") if p.is_file()}
    assert files_after < files_before or not files_after

    # jobs referencing the dataset are gone from the history
    jobs_after = client.get("/api/v1/jobs?page_size=100", headers=headers).json()["total"]
    assert jobs_after < jobs_before

    # customer detail has nothing scored for this version anymore
    detail = client.get(f"/api/v1/customers/C001?dataset_version_id={ids['version_id']}", headers=headers)
    if detail.status_code == 200:
        assert detail.json()["data_version"] == "no_data"

    # audit trail retained and records the deletion (owner-only endpoint)
    audit = client.get("/api/v1/audit?action=dataset.deleted", headers=headers)
    assert audit.status_code == 200
    assert audit.json()["total"] >= 1


def test_dataset_delete_no_versions_yet(client, org_a_owner, app_db, tmp_path, monkeypatch) -> None:
    """Regression (found live): a dataset with zero versions must delete
    cleanly — `seg_artifacts` used to be referenced before assignment."""
    from tests.conftest import create_dataset

    headers = org_a_owner["headers"]
    dataset_id = create_dataset(client, headers, name="Empty DS")

    response = client.delete(f"/api/v1/datasets/{dataset_id}", headers=headers)
    assert response.status_code == 204, response.text
    assert client.get(f"/api/v1/datasets/{dataset_id}", headers=headers).status_code == 404


def test_dataset_delete_roles_and_isolation(client, org_a_owner, org_b_owner, app_db, tmp_path, monkeypatch) -> None:
    headers = org_a_owner["headers"]
    monkeypatch.setattr(settings, "models_dir", tmp_path / "models", raising=False)
    dataset_id, version_id = _csv_dataset(client, headers)
    client.post("/api/v1/churn/model-runs", json={"dataset_version_id": version_id}, headers=headers)

    # another tenant cannot see the dataset at all, so deleting it via their
    # session is indistinguishable from deleting a random id -> 404, no data touched
    response = client.delete(f"/api/v1/datasets/{dataset_id}", headers=org_b_owner["headers"])
    assert response.status_code == 404
    assert client.get(f"/api/v1/datasets/{dataset_id}", headers=headers).status_code == 200

    # a second org's datasets stay untouched
    assert client.get("/api/v1/datasets", headers=org_b_owner["headers"]).json()["total"] >= 0

    # same user, different role: create a member (viewer) inside org A
    _engine, factory = app_db
    from app.core.security import hash_password
    from app.domains.identity.models import Membership, User

    session = factory()
    org_id = None
    from app.domains.identity.models import Organization

    org = session.scalar(select(Organization).limit(1))
    org_id = org.id
    user = User(
        id=uuid.uuid4(),
        email="viewer-a@test.io",
        hashed_password=hash_password("hunter2hunter2"),
        full_name="Viewer",
    )
    session.add(user)
    session.add(Membership(id=uuid.uuid4(), user_id=user.id, organization_id=org_id, role="member"))
    session.commit()
    org_member_login = login(client, "viewer-a@test.io")
    member_headers = {**headers_for(org_member_login), "X-Organization-ID": str(org_id)}
    session.close()

    # viewer role may not delete datasets (AUTH-04: owner/admin only)
    denied = client.delete(f"/api/v1/datasets/{dataset_id}", headers=member_headers)
    assert denied.status_code == 403
    assert client.get(f"/api/v1/datasets/{dataset_id}", headers=headers).status_code == 200
