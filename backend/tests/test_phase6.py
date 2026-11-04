"""Phase 6 tests: API/jobs hardening (API-05..12, JOB-01..10, SEC-01..06).

Covers the newly built Phase-6 surface: churn model-run API, jobs list,
score ordering, CSV exports (with download + audit), customer detail,
rate limiting, log redaction, and run failure hardening (JOB-09).
"""

from __future__ import annotations

import json
import logging
import uuid as uuid_module

import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from tests.test_training import _csv_dataset

# --- API-06: churn training runs exposed over API -----------------------------


def _prepare(client, org) -> tuple[str, str]:
    headers = org["headers"]
    dataset_id, version_id = _csv_dataset(client, headers)
    return dataset_id, version_id


@pytest.fixture()
def trained_model_run(client, org_a_owner) -> dict[str, str]:
    dataset_id, version_id = _prepare(client, org_a_owner)
    response = client.post(
        "/api/v1/churn/model-runs",
        json={"dataset_version_id": version_id, "min_period_customers": 10},
        headers=org_a_owner["headers"],
    )
    assert response.status_code == 202, response.text
    body = response.json()["run"]
    assert body["status"] == "succeeded"  # inline job
    return {"run_id": body["id"], "version_id": version_id, "dataset_id": dataset_id}


def test_churn_model_run_api_lifecycle(client, org_a_owner, org_b_owner, trained_model_run) -> None:
    headers = org_a_owner["headers"]
    run_id = trained_model_run["run_id"]

    detail = client.get(f"/api/v1/churn/model-runs/{run_id}", headers=headers)
    assert detail.status_code == 200
    body = detail.json()["run"]
    assert body["status"] == "succeeded"
    assert body["metrics"] and "selection" in body["metrics"]  # CHURN-17 registry

    listing = client.get("/api/v1/churn/model-runs", headers=headers)
    assert listing.status_code == 200
    assert listing.json()["total"] >= 1

    cross = client.get(f"/api/v1/churn/model-runs/{run_id}", headers=org_b_owner["headers"])
    assert cross.status_code == 404  # SEC-01: cross-tenant read hidden

    client.post(
        "/api/v1/churn/scores",
        json={"model_run_id": run_id},
        headers=headers,
    )
    scored = client.get(
        f"/api/v1/churn/scores?model_run_id={run_id}&order=asc",
        headers=headers,
    )
    assert scored.status_code == 200
    probs = [row["churn_probability"] for row in scored.json()["items"]]
    assert probs == sorted(probs)  # API-11 bounded ordering honored
    scored_desc = client.get(
        f"/api/v1/churn/scores?model_run_id={run_id}&order=desc",
        headers=headers,
    )
    probs_desc = [row["churn_probability"] for row in scored_desc.json()["items"]]
    assert probs_desc == sorted(probs_desc, reverse=True)


# --- API-11: jobs list endpoint ------------------------------------------------


def test_jobs_list_scoped_filtered_paginated(client, org_a_owner, org_b_owner, trained_model_run) -> None:
    headers = org_a_owner["headers"]
    listing = client.get("/api/v1/jobs", headers=headers)
    assert listing.status_code == 200
    body = listing.json()
    assert body["total"] >= 1
    assert {item["type"] for item in body["items"]} <= {
        "validate_dataset",
        "compute_features",
        "train_churn_model",
        "score_churn_model",
        "run_segmentation",
        "export_csv",
    }
    filtered = client.get("/api/v1/jobs?job_type=train_churn_model&status=succeeded", headers=headers)
    assert filtered.status_code == 200
    for item in filtered.json()["items"]:
        assert item["type"] == "train_churn_model"
    other = client.get("/api/v1/jobs", headers=org_b_owner["headers"])
    assert other.status_code == 200
    assert other.json()["total"] == 0  # SEC-01: org-scoped


# --- API-10/JOB-06 + SEC-06: CSV export + audit trail ---------------------------


def test_export_churn_scores_download_and_audit(client, org_a_owner, org_b_owner, trained_model_run) -> None:
    headers = org_a_owner["headers"]
    run_id = trained_model_run["run_id"]
    client.post("/api/v1/churn/scores", json={"model_run_id": run_id}, headers=headers)

    created = client.post(
        "/api/v1/exports",
        json={"kind": "churn_scores", "run_id": run_id},
        headers=headers,
    )
    assert created.status_code == 202, created.text
    job = created.json()["job"]
    assert job["job_type"] == "export_csv"
    assert job["status"] == "succeeded"  # inline

    downloaded = client.get(f"/api/v1/exports/{job['id']}/download", headers=headers)
    assert downloaded.status_code == 200
    assert downloaded.headers["content-type"].startswith("text/csv")
    lines = downloaded.text.strip().splitlines()
    assert lines[0].startswith("customer_id,churn_probability,risk_band")
    assert len(lines) >= 2

    # SEC-01: cross-tenant export + download are hidden
    denied = client.post(
        "/api/v1/exports", json={"kind": "churn_scores", "run_id": run_id}, headers=org_b_owner["headers"]
    )
    assert denied.status_code == 404
    denied_download = client.get(f"/api/v1/exports/{job['id']}/download", headers=org_b_owner["headers"])
    assert denied_download.status_code == 404

    bad = client.post("/api/v1/exports", json={"kind": "weapons", "run_id": run_id}, headers=headers)
    assert bad.status_code == 400

    # SEC-06: audit rows for the export lifecycle
    audit = client.get("/api/v1/audit", headers=headers).json()
    actions = {row["action"] for row in audit["items"]}
    assert "export.requested" in actions
    assert "export.downloaded" in actions


def test_export_segment_assignments(client, org_a_owner, app_db) -> None:
    _, factory = app_db
    headers = org_a_owner["headers"]
    dataset_id, version_id = _prepare(client, org_a_owner)
    import uuid as uuid_mod

    seg = client.post(
        "/api/v1/segmentation/runs",
        json={"dataset_version_id": version_id, "k_candidates": [3]},
        headers=headers,
    )
    assert seg.status_code == 202, seg.text
    run_id = seg.json()["run"]["id"]
    assert seg.json()["run"]["status"] == "succeeded"

    created = client.post(
        "/api/v1/exports",
        json={"kind": "segment_assignments", "run_id": run_id},
        headers=headers,
    )
    assert created.status_code == 202
    job = created.json()["job"]
    downloaded = client.get(f"/api/v1/exports/{job['id']}/download", headers=headers)
    assert downloaded.status_code == 200
    first_line = downloaded.text.splitlines()[0]
    assert first_line.startswith("customer_id,cluster_id,segment_label")
    _ = uuid_mod  # kept for parity with other tests' idiom


def test_download_requires_succeeded_job(client, org_a_owner, app_db) -> None:
    _, factory = app_db

    from app.domains.audit import service as audit_service  # noqa: F401

    missing = client.get(f"/api/v1/exports/{uuid_module.uuid4()}/download", headers=org_a_owner["headers"])
    assert missing.status_code == 404


# --- SEC-06: audit events for run creation --------------------------------------


def test_audit_records_run_creation(client, org_a_owner, org_b_owner, trained_model_run) -> None:
    headers = org_a_owner["headers"]
    audit = client.get("/api/v1/audit", headers=headers)
    assert audit.status_code == 200
    actions = {row["action"] for row in audit.json()["items"]}
    assert "churn.run_requested" in actions
    # org-scoped (SEC-01/SEC-06)
    other = client.get("/api/v1/audit", headers=org_b_owner["headers"]).json()
    assert other["total"] == 0


def test_audit_records_segmentation_run(client, org_a_owner) -> None:
    headers = org_a_owner["headers"]
    dataset_id, version_id = _prepare(client, org_a_owner)
    seg = client.post(
        "/api/v1/segmentation/runs",
        json={"dataset_version_id": version_id, "k_candidates": [3]},
        headers=headers,
    )
    assert seg.status_code == 202
    actions = {row["action"] for row in client.get("/api/v1/audit", headers=headers).json()["items"]}
    assert "segmentation.run_requested" in actions


# --- API-09: customer detail ------------------------------------------------------


def test_customer_detail_unified_view(client, org_a_owner, org_b_owner, trained_model_run) -> None:
    headers = org_a_owner["headers"]
    run_id = trained_model_run["run_id"]
    version_id = trained_model_run["version_id"]

    client.post("/api/v1/churn/scores", json={"model_run_id": run_id}, headers=headers)
    scored = client.get(f"/api/v1/churn/scores?model_run_id={run_id}", headers=headers).json()
    customer_id = scored["items"][0]["customer_id"]

    detail = client.get(f"/api/v1/customers/{customer_id}?dataset_version_id={version_id}", headers=headers)
    assert detail.status_code == 200
    body = detail.json()
    assert body["data_version"] == "score_only"
    assert body["churn"]["customer_id"] == customer_id
    assert body["churn"]["risk_band"] in ("low", "medium", "high")
    assert body["churn"]["explanation"]["top_signals"]  # CHURN-16 payload attached
    assert "not causal" in str(body["churn"]["explanation"])
    assert body["segment"] is None

    # SEC-01: hidden from other orgs
    cross = client.get(
        f"/api/v1/customers/{customer_id}?dataset_version_id={version_id}",
        headers=org_b_owner["headers"],
    )
    assert cross.status_code == 404


def test_customer_detail_with_segmentation(client, org_a_owner, trained_model_run) -> None:
    headers = org_a_owner["headers"]
    run_id = trained_model_run["run_id"]
    version_id = trained_model_run["version_id"]
    client.post("/api/v1/churn/scores", json={"model_run_id": run_id}, headers=headers)
    seg = client.post(
        "/api/v1/segmentation/runs",
        json={"dataset_version_id": version_id, "k_candidates": [3]},
        headers=headers,
    )
    assert seg.status_code == 202

    scored = client.get(f"/api/v1/churn/scores?model_run_id={run_id}", headers=headers).json()
    customer_id = scored["items"][0]["customer_id"]
    body = client.get(f"/api/v1/customers/{customer_id}?dataset_version_id={version_id}", headers=headers).json()
    assert body["data_version"] == "score+segment"
    assert body["segment"]["segment_label"]


# --- JOB-09: failed runs never stay stuck 'running' --------------------------------


def test_churn_training_failure_marks_run_failed(client, org_a_owner, app_db, monkeypatch) -> None:
    import app.domains.churn.service as churn_service

    dataset_id, version_id = _prepare(client, org_a_owner)

    def boom(*args, **kwargs):  # noqa: ANN002, ANN003
        raise RuntimeError("engine exploded")

    monkeypatch.setattr(churn_service, "train_churn_models", boom)
    response = client.post(
        "/api/v1/churn/model-runs",
        json={"dataset_version_id": version_id, "min_period_customers": 10},
        headers=org_a_owner["headers"],
    )
    assert response.status_code == 202  # job queued; inline run_job records failure
    run = response.json()["run"]
    assert run["status"] == "failed"
    assert "engine exploded" in run["error"]


def test_segmentation_failure_marks_run_failed(client, org_a_owner, monkeypatch) -> None:
    import app.domains.segmentation.service as seg_service

    dataset_id, version_id = _prepare(client, org_a_owner)

    def boom(*args, **kwargs):  # noqa: ANN002, ANN003
        raise RuntimeError("cluster engine exploded")

    monkeypatch.setattr(seg_service, "run_segmentation", boom)
    response = client.post(
        "/api/v1/segmentation/runs",
        json={"dataset_version_id": version_id, "k_candidates": [3]},
        headers=org_a_owner["headers"],
    )
    assert response.status_code == 202
    run = response.json()["run"]
    assert run["status"] == "failed"
    assert "cluster engine exploded" in run["error"]


# --- SEC-04: rate limiting ----------------------------------------------------------


def test_rate_limit_buckets(client, app_db, monkeypatch) -> None:
    from app.main import create_app

    _, factory = app_db
    from app.db.session import get_db

    monkeypatch.setattr(settings, "rate_limit_enabled", True, raising=False)
    monkeypatch.setattr(settings, "rate_limit_auth_per_minute", 2, raising=False)
    monkeypatch.setattr(settings, "rate_limit_expensive_per_minute", 1, raising=False)
    fresh_app = create_app()

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

    fresh_app.dependency_overrides[get_db] = override_get_db
    with TestClient(fresh_app, raise_server_exceptions=False) as test_client:
        # auth bucket: third login attempt is limited
        for expected in (401, 401):
            r = test_client.post("/api/v1/auth/login", json={"email": "x@x.io", "password": "nope-nope-nope"})
            assert r.status_code == expected
        limited = test_client.post("/api/v1/auth/login", json={"email": "x@x.io", "password": "nope-nope-nope"})
        assert limited.status_code == 429
        assert limited.json()["error"]["code"] == "RATE_LIMITED"

        # expensive bucket (a different bucket key, not entangled with auth)
        r = test_client.post("/api/v1/churn/scores", json={})
        assert r.status_code in (401, 403, 422)  # auth failure, not limited yet
        r2 = test_client.post("/api/v1/churn/scores", json={})
        assert r2.status_code == 429


def test_rate_limit_disabled_by_default(client) -> None:
    # fixtures already exercise many requests per test; default-off means
    # no 429s appear in a normal test session.
    for _ in range(3):
        response = client.get("/api/v1/jobs", headers={})
        assert response.status_code in (401, 403)
        assert response.status_code != 429


# --- SEC-05: log redaction -----------------------------------------------------------


def test_json_formatter_redacts_secret_looking_extras() -> None:
    from app.core.logging import JSONFormatter

    formatter = JSONFormatter()
    record = logging.LogRecord(
        name="t", level=logging.INFO, pathname="x.py", lineno=1, msg="hi", args=(), exc_info=None
    )
    record.password = "hunter2"
    record.refresh_token = "tok123"
    record.customer = {"email": "a@b.c", "token": "inner-secret"}
    record.plain = "safe value"

    payload = json.loads(formatter.format(record))
    assert payload["password"] == "***redacted***"
    assert payload["refresh_token"] == "***redacted***"
    assert payload["customer"]["token"] == "***redacted***"
    assert payload["customer"]["email"] == "a@b.c"
    assert payload["plain"] == "safe value"
    assert "hunter2" not in json.dumps(payload)
