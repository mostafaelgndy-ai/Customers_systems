"use client";

// Exports (UI-13): request a CSV export of a run's results and download
// the produced file. Downloads stream via the authed API client.

import { useState } from "react";

import { api, ApiError, API_URL } from "../../lib/api";
import { formatDate, useAuthGuard, useApiResource } from "../../lib/hooks";
import { PageStates } from "../../components/states";

type ModelRun = { id: string; status: string; train_cutoff_date: string | null };
type SegmentRun = { id: string; status: string; selected_k: number | null };

export default function ExportsPage() {
  useAuthGuard();

  const churnRuns = useApiResource<{ items: ModelRun[] }>("/api/v1/churn/model-runs?page=1&page_size=50", []);
  const segRuns = useApiResource<{ items: SegmentRun[] }>("/api/v1/segmentation/runs?page=1&page_size=50", []);
  const jobs = useApiResource<{ items: Array<{ id: string; type: string; status: string; progress: number; created_at: string }>; total: number }>(
    "/api/v1/jobs?job_type=export_csv&page=1&page_size=25",
    []
  );

  const succeededChurn = churnRuns.data?.items.filter((run) => run.status === "succeeded") ?? [];
  const succeededSeg = segRuns.data?.items.filter((run) => run.status === "succeeded") ?? [];

  const [kind, setKind] = useState<"churn_scores" | "segment_assignments">("churn_scores");
  const [runId, setRunId] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [requestError, setRequestError] = useState<string | null>(null);

  const runOptions = kind === "churn_scores" ? succeededChurn : succeededSeg;

  async function createExport() {
    if (!runId) {
      setRequestError("Pick a run to export first.");
      return;
    }
    setBusy(true);
    setRequestError(null);
    setMessage(null);
    try {
      const response = await api.post<{ job: { id: string; status: string } }>("/api/v1/exports", {
        kind,
        run_id: runId,
      });
      setMessage(
        response.job.status === "succeeded"
          ? "Export ready — download it below."
          : "Export queued — the file will appear below when the job finishes."
      );
      jobs.reload();
    } catch (err) {
      setRequestError(err instanceof ApiError ? err.message : "Could not request the export.");
    } finally {
      setBusy(false);
    }
  }

  async function download(jobId: string) {
    setRequestError(null);
    try {
      const blob = await api.download(`/api/v1/exports/${jobId}/download`);
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = `customeriq_export_${jobId.slice(0, 8)}.csv`;
      // Safari/Firefox ignore clicks on detached elements; attach first.
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(url);
    } catch (err) {
      setRequestError(err instanceof ApiError ? err.message : "Download failed.");
    }
  }

  return (
    <>
      <h1>Exports</h1>
      <p className="subtitle">
        Download the results of one analysis run as CSV. Exported files are traceable to their run
        and organization.
      </p>

      <div className="card">
        <h2>New export</h2>
        <div className="field-row" style={{ maxWidth: "34rem" }}>
          <label className="field">
            <span className="field-label">What to export</span>
            <select
              className="input"
              value={kind}
              onChange={(e) => {
                setKind(e.target.value as "churn_scores" | "segment_assignments");
                setRunId("");
              }}
            >
              <option value="churn_scores">Churn scores (from a model run)</option>
              <option value="segment_assignments">Segment assignments (from a segmentation run)</option>
            </select>
          </label>
          <label className="field">
            <span className="field-label">Run</span>
            <select className="input" value={runId} onChange={(e) => setRunId(e.target.value)}>
              <option value="">— select a succeeded run —</option>
              {runOptions.map((run) => (
                <option key={run.id} value={run.id}>
                  {run.id.slice(0, 8)}…{" "}
                  {"train_cutoff_date" in run && run.train_cutoff_date
                    ? `cutoff ${run.train_cutoff_date}`
                    : "selected_k" in run && run.selected_k != null
                      ? `k=${run.selected_k}`
                      : ""}
                </option>
              ))}
            </select>
          </label>
        </div>
        {message && <div className="info-banner" style={{ marginTop: "0.75rem" }}>{message}</div>}
        {requestError && <div className="error-banner" style={{ marginTop: "0.75rem" }}>{requestError}</div>}
        <div className="btn-row" style={{ marginTop: "0.75rem" }}>
          <button
            type="button"
            className="btn btn-primary"
            disabled={busy}
            onClick={() => void createExport()}
          >
            {busy ? "Requesting…" : "Request export"}
          </button>
        </div>
      </div>

      <div className="card section">
        <h2>Export history</h2>
        <PageStates
          loading={jobs.loading}
          error={jobs.error}
          empty={!!jobs.data && jobs.data.total === 0}
          emptyHint="No exports requested yet."
          onRetry={jobs.reload}
        >
          {jobs.data && jobs.data.items.length > 0 && (
            <div className="table-wrap">
              <table className="data">
                <thead>
                  <tr>
                    <th>Export job</th>
                    <th>Status</th>
                    <th>Created</th>
                    <th></th>
                  </tr>
                </thead>
                <tbody>
                  {jobs.data.items.map((job) => (
                    <tr key={job.id}>
                      <td>
                        <code>{job.id.slice(0, 8)}…</code>
                      </td>
                      <td className={`badge ${job.status === "succeeded" ? "badge-ok" : job.status === "failed" ? "badge-high" : "badge-progress"}`}>
                        {job.status}
                      </td>
                      <td>{formatDate(job.created_at)}</td>
                      <td>
                        {job.status === "succeeded" && (
                          <button
                            type="button"
                            className="btn btn-secondary btn-small"
                            onClick={() => void download(job.id)}
                          >
                            Download CSV
                          </button>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </PageStates>
      </div>
    </>
  );
}
