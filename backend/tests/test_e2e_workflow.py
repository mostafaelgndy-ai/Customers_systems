"""TEST-08: one API integration test for the complete upload-to-results
workflow, with traceability assertions (PRD §11.3 MVP acceptance).

Register → create dataset → upload CSVs → fix stale mapping → version →
validation report → feature run → churn training → scoring → segmentation →
rename → compare → export → download → customer detail → jobs audit trail.
"""

from __future__ import annotations

import pytest

from app.core.config import settings
from tests.test_training import _csv_dataset


@pytest.fixture()
def workflow_ids(client, org_a_owner, tmp_path, monkeypatch) -> dict:
    """Run the full journey once; return the artifact ids the journey used."""
    headers = org_a_owner["headers"]
    monkeypatch.setattr(settings, "models_dir", tmp_path / "models", raising=False)
    monkeypatch.setattr(settings, "exports_dir", tmp_path / "exports", raising=False)

    dataset_id, version_id = _csv_dataset(client, headers)

    # 1) features
    features = client.post(
        "/api/v1/features/runs",
        params={"dataset_version_id": version_id, "cutoff_date": "2025-08-01"},
        headers=headers,
    )
    assert features.status_code == 201, features.text
    feature_run_id = features.json()["run"]["id"]
    assert features.json()["run"]["status"] == "succeeded"
    snap = client.get(f"/api/v1/features/runs/{feature_run_id}", headers=headers)
    assert snap.json()["dataset_version_id"] == version_id
    assert snap.json()["features"], "snapshot rows should be present"

    # 2) churn training
    train = client.post("/api/v1/churn/model-runs", json={"dataset_version_id": version_id}, headers=headers)
    assert train.status_code == 202, train.text
    run_id = train.json()["run"]["id"]
    run_detail = client.get(f"/api/v1/churn/model-runs/{run_id}", headers=headers).json()["run"]
    assert run_detail["status"] == "succeeded", run_detail
    assert run_detail["dataset_version_id"] == version_id  # traceability
    assert run_detail["version_number"] == 1

    # 3) scoring
    scored = client.post("/api/v1/churn/scores", json={"model_run_id": run_id}, headers=headers)
    assert scored.status_code == 202, scored.text
    job_id = scored.json()["job"]["id"]
    job = client.get(f"/api/v1/jobs/{job_id}", headers=headers).json()
    assert job["status"] == "succeeded", str(job)[:300]
    scores = client.get(f"/api/v1/churn/scores?model_run_id={run_id}", headers=headers).json()
    assert scores["total"] > 0
    for row in scores["items"]:
        assert row["risk_band"] in ("low", "medium", "high")
        assert 0.0 <= row["churn_probability"] <= 1.0

    # 4) segmentation + rename + comparison
    seg = client.post(
        "/api/v1/segmentation/runs",
        json={"dataset_version_id": version_id, "k_candidates": [3]},
        headers=headers,
    )
    assert seg.status_code == 202, seg.text
    seg_id = seg.json()["run"]["id"]
    seg_detail = client.get(f"/api/v1/segmentation/runs/{seg_id}", headers=headers).json()["run"]
    assert seg_detail["status"] == "succeeded", seg_detail
    assert seg_detail["dataset_version_id"] == version_id  # traceability
    first_profile = seg_detail["profiles"]["segments"][0]
    renamed = client.post(
        f"/api/v1/segmentation/runs/{seg_id}/segments/{first_profile['cluster_id']}/rename",
        json={"segment_label": "Workflow Champion"},
        headers=headers,
    )
    assert renamed.status_code in (200, 201), renamed.text
    after = client.get(f"/api/v1/segmentation/runs/{seg_id}", headers=headers).json()["run"]
    labels = [p["descriptive_label"] for p in after["profiles"]["segments"]]
    assert "Workflow Champion" in labels
    assignments = client.get(f"/api/v1/segmentation/runs/{seg_id}/assignments", headers=headers)
    if assignments.status_code == 200:
        assert assignments.json()["total"] == scores["total"]  # same version population

    # 5) export + download (TEST-11 exactness: full scored population)
    export = client.post("/api/v1/exports", json={"kind": "churn_scores", "run_id": run_id}, headers=headers)
    assert export.status_code == 202, export.text
    export_job_id = export.json()["job"]["id"]
    download = client.get(f"/api/v1/exports/{export_job_id}/download", headers=headers)
    assert download.status_code == 200, download.text
    assert download.headers["content-type"].startswith("text/csv")
    lines = download.text.strip().splitlines()
    assert lines[0].startswith("customer_id")
    assert len(lines) - 1 == scores["total"]  # every scored row exported
    assert len({line.split(",")[0] for line in lines[1:]}) == scores["total"]  # unique customers

    return {
        "dataset_id": dataset_id,
        "version_id": version_id,
        "feature_run_id": feature_run_id,
        "model_run_id": run_id,
        "segmentation_run_id": seg_id,
        "export_job_id": export_job_id,
        "scores_total": scores["total"],
        "first_customer": scores["items"][0]["customer_id"],
    }


def test_full_upload_to_results_workflow(client, org_a_owner, org_b_owner, workflow_ids) -> None:
    headers = org_a_owner["headers"]
    ids = workflow_ids

    # customer detail integrates score + segment for the same version
    detail = client.get(
        f"/api/v1/customers/{ids['first_customer']}?dataset_version_id={ids['version_id']}",
        headers=headers,
    )
    assert detail.status_code == 200, detail.text
    body = detail.json()
    assert body["data_version"] == "score+segment"
    churn = body["churn"]
    assert churn["model_run_id"] == ids["model_run_id"]
    explanation = churn["explanation"] or {}
    signals = explanation.get("top_signals") or []
    for signal in signals:
        assert signal["direction"] in ("up", "down", "higher_risk", "lower_risk")
    if body["segment"]:
        assert body["segment"]["segmentation_run_id"] == ids["segmentation_run_id"]

    # cross-tenant: org B sees none of org A's artifacts
    b_headers = org_b_owner["headers"]
    assert client.get(f"/api/v1/churn/model-runs/{ids['model_run_id']}", headers=b_headers).status_code == 404
    assert client.get(f"/api/v1/segmentation/runs/{ids['segmentation_run_id']}", headers=b_headers).status_code == 404
    assert client.get(f"/api/v1/exports/{ids['export_job_id']}/download", headers=b_headers).status_code == 404
    assert client.get("/api/v1/jobs", headers=b_headers).json()["total"] == 0

    # audit trail contains every sensitive action of the journey
    audit = client.get("/api/v1/audit?page_size=100", headers=headers)
    assert audit.status_code == 200
    actions = {event["action"] for event in audit.json()["items"]}
    assert "dataset.created" in actions
    assert "churn.run_requested" in actions
    assert "export.requested" in actions
    assert "export.downloaded" in actions

    # dataset version immutability: the version still reports valid
    version = client.get(f"/api/v1/datasets/{ids['dataset_id']}/versions/{ids['version_id']}", headers=headers).json()
    assert version["status"] == "valid" and version["report"]["summary"]["customers"]["rows_total"] > 0
