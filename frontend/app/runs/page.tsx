"use client";

// Background job tracking (UI-07): list every queued/running/finished job
// with progress, filterable by type and status; auto-refreshes while
// anything is still in flight.

import { useEffect, useState } from "react";

import { useApiResource, useAuthGuard, STATUS_COLORS, formatDate } from "../../lib/hooks";
import { PageStates } from "../../components/states";

type Job = {
  id: string;
  type: string;
  status: string;
  progress: number;
  created_at: string;
};

type JobList = {
  items: Job[];
  page: number;
  page_size: number;
  pages: number;
  total: number;
};

const JOB_TYPES = ["", "validate_dataset", "compute_features", "train_churn_model", "score_churn_model", "run_segmentation", "export_csv"];
const STATUSES = ["", "queued", "running", "succeeded", "failed", "cancelled"];
const REFRESH_MS = 5000;

export default function RunsPage() {
  useAuthGuard();
  const [jobType, setJobType] = useState("");
  const [status, setStatus] = useState("");
  const [page, setPage] = useState(1);

  const query = new URLSearchParams();
  if (jobType) query.set("job_type", jobType);
  if (status) query.set("status", status);
  query.set("page", String(page));
  query.set("page_size", "25");

  const { data, loading, error, reload } = useApiResource<JobList>(
    `/api/v1/jobs?${query.toString()}`,
    [jobType, status, page]
  );

  const activeCount = data?.items.filter((job) => job.status === "queued" || job.status === "running").length ?? 0;

  // Auto-refresh while something is queued/running (UI-07 progress).
  useEffect(() => {
    if (activeCount === 0) return;
    const timer = setInterval(reload, REFRESH_MS);
    return () => clearInterval(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeCount]);

  return (
    <>
      <h1>Runs & jobs</h1>
      <p className="subtitle">
        Every analysis runs as a tracked background job: dataset validation, feature building, churn
        training/scoring, segmentation, and exports.
      </p>

      <div className="field-row" style={{ maxWidth: "32rem" }}>
        <label className="field">
          <span className="field-label">Type</span>
          <select
            className="input"
            value={jobType}
            onChange={(e) => {
              setJobType(e.target.value);
              setPage(1);
            }}
          >
            {JOB_TYPES.map((t) => (
              <option key={t} value={t}>
                {t || "all types"}
              </option>
            ))}
          </select>
        </label>
        <label className="field">
          <span className="field-label">Status</span>
          <select
            className="input"
            value={status}
            onChange={(e) => {
              setStatus(e.target.value);
              setPage(1);
            }}
          >
            {STATUSES.map((s) => (
              <option key={s} value={s}>
                {s || "all statuses"}
              </option>
            ))}
          </select>
        </label>
      </div>

      <div className="section">
        <PageStates
          loading={loading}
          error={error}
          empty={!loading && !error && !!data && data.total === 0}
          emptyHint="No jobs yet. Upload a dataset and start an analysis run."
          onRetry={reload}
        >
          {data && (
            <>
              {activeCount > 0 && (
                <div className="info-banner" style={{ marginBottom: "0.75rem" }}>
                  {activeCount} job{activeCount > 1 ? "s" : ""} in flight — refreshing every{" "}
                  {REFRESH_MS / 1000}s.
                </div>
              )}
              <div className="table-wrap">
                <table className="data">
                  <thead>
                    <tr>
                      <th>Job</th>
                      <th>Type</th>
                      <th>Status</th>
                      <th>Progress</th>
                      <th>Created</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.items.map((job) => (
                      <tr key={job.id}>
                        <td>
                          <code>{job.id.slice(0, 8)}…</code>
                        </td>
                        <td>{job.type}</td>
                        <td>
                          <span className={`badge ${STATUS_COLORS[job.status] ?? ""}`}>
                            {job.status}
                          </span>
                        </td>
                        <td>{job.progress}%</td>
                        <td>{formatDate(job.created_at)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <div className="btn-row" style={{ marginTop: "0.75rem" }}>
                <button
                  type="button"
                  className="btn btn-secondary btn-small"
                  disabled={page <= 1}
                  onClick={() => setPage((p) => Math.max(1, p - 1))}
                >
                  ← Previous
                </button>
                <span className="hint">
                  Page {data.page} of {data.pages || 1} ({data.total} jobs)
                </span>
                <button
                  type="button"
                  className="btn btn-secondary btn-small"
                  disabled={page >= data.pages}
                  onClick={() => setPage((p) => p + 1)}
                >
                  Next →
                </button>
              </div>
            </>
          )}
        </PageStates>
      </div>
    </>
  );
}
