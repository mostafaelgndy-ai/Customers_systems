"""CSV export domain (API-10/JOB-06).

Exports the results of one analysis run (churn scores or segment
assignments) to CSV:
  POST /api/v1/exports                 — queue an export job (202)
  GET  /api/v1/exports/{job_id}/download — stream the produced CSV

Exports are organization-scoped, traceable to the run they came from,
and auditable via the job record. Only ancestor-scoped rows are ever
written; the download endpoint serves exactly the path recorded on the
succeeded job (never a caller-controlled path).
"""
