"use client";

// Segmentation overview (UI-11): segmentation runs, selected k, segment
// distribution bars, and the rename action (SEG-08 editable labels).

import { useState } from "react";

import { api, ApiError } from "../../lib/api";
import { useAuthGuard, useApiResource } from "../../lib/hooks";
import { PageStates } from "../../components/states";

type SegmentRun = {
  id: string;
  status: string;
  dataset_version_id: string;
  selected_k: number | null;
  cutoff_date: string | null;
  observation_window_days: number;
};

type Profile = {
  cluster_id: number;
  count: number;
  share: number;
  descriptive_label: string;
  label_editable?: boolean;
};

type RunDetail = {
  id: string;
  status: string;
  profiles: Profile[] | null;
  warnings: string[] | null;
  candidate_report: unknown;
  error: string | null;
};

export default function SegmentsPage() {
  useAuthGuard();
  const { data, loading, error, reload } = useApiResource<{ items: SegmentRun[]; total: number }>(
    "/api/v1/segmentation/runs?page=1&page_size=50",
    []
  );
  const [openRunId, setOpenRunId] = useState<string>("");

  const runs = data?.items ?? [];
  const selected =
    openRunId || (runs[0]?.status === "succeeded" ? runs[0].id : "");

  return (
    <>
      <h1>Segments</h1>
      <p className="subtitle">
        Behavioral segmentation of your customers. Profile statistics are descriptive — labels are
        editable, cluster ids are arbitrary.
      </p>

      <PageStates
        loading={loading}
        error={error}
        empty={!loading && !error && runs.length === 0}
        emptyHint="No segmentation runs yet — open a validated dataset version and run segmentation."
        onRetry={reload}
      >
        <div className="card">
          <div className="field-row" style={{ maxWidth: "30rem" }}>
            <label className="field">
              <span className="field-label">Segmentation run</span>
              <select
                className="input"
                value={selected}
                onChange={(e) => setOpenRunId(e.target.value)}
              >
                {runs
                  .filter((run) => run.status === "succeeded")
                  .map((run) => (
                    <option key={run.id} value={run.id}>
                      {run.id.slice(0, 8)}… (k={run.selected_k ?? "—"}, version{" "}
                      {run.dataset_version_id.slice(0, 8)}…)
                    </option>
                  ))}
              </select>
            </label>
          </div>
        </div>

        {selected && <RunProfiles runId={selected} />}
      </PageStates>
    </>
  );
}

function RunProfiles({ runId }: { runId: string }) {
  const { data, loading, error, reload } = useApiResource<{ run: RunDetail }>(
    `/api/v1/segmentation/runs/${runId}`,
    [runId]
  );
  const run = data?.run;
  const profiles = run?.profiles ?? [];

  const [renaming, setRenaming] = useState<number | null>(null);
  const [newLabel, setNewLabel] = useState("");
  const [actionError, setActionError] = useState<string | null>(null);

  async function renameProfile(clusterId: number) {
    setActionError(null);
    try {
      await api.post(`/api/v1/segmentation/runs/${runId}/segments/${clusterId}/rename`, {
        segment_label: newLabel,
      });
      setRenaming(null);
      reload();
    } catch (err) {
      setActionError(
        err instanceof ApiError ? err.message : "Could not rename the segment."
      );
    }
  }

  if (loading) return <LoadingLine />;
  if (error) return <div className="card section error-banner">{error.message}</div>;
  if (!run) return null;

  if (run.status !== "succeeded") {
    return (
      <div className="card section">
        <p className="subtitle">
          This run is {run.status}
          {run.error ? `: ${run.error}` : ""}.
        </p>
      </div>
    );
  }

  return (
    <div className="card section">
      <h2>Segment distribution</h2>
      {run.warnings && run.warnings.length > 0 && (
        <div className="info-banner" style={{ marginBottom: "0.75rem" }}>
          {run.warnings.join(" · ")}
        </div>
      )}
      {profiles.length === 0 ? (
        <p className="subtitle">Profiles not available for this run yet.</p>
      ) : (
        profiles.map((profile) => (
          <div key={profile.cluster_id} className="bar-wrap">
            <strong style={{ minWidth: "9rem" }}>
              {profile.descriptive_label || `Cluster ${profile.cluster_id}`}
            </strong>
            <div className="bar-track">
              <div className="bar-fill bar-neutral" style={{ width: `${profile.share * 100}%` }} />
            </div>
            <span>
              {profile.count} ({(profile.share * 100).toFixed(1)}%)
            </span>
            {profile.label_editable !== false && (
              <button
                type="button"
                className="btn btn-secondary btn-small"
                onClick={() => {
                  setRenaming(profile.cluster_id);
                  setNewLabel(profile.descriptive_label || "");
                }}
              >
                Rename
              </button>
            )}
          </div>
        ))
      )}

      {actionError && <div className="error-banner">{actionError}</div>}

      {renaming !== null && (
        <div className="field-row" style={{ maxWidth: "28rem", marginTop: "0.75rem" }}>
          <label className="field">
            <span className="field-label">New label for cluster #{renaming}</span>
            <input
              className="input"
              type="text"
              value={newLabel}
              onChange={(e) => setNewLabel(e.target.value)}
            />
          </label>
          <div className="btn-row" style={{ alignItems: "flex-end" }}>
            <button
              type="button"
              className="btn btn-primary"
              onClick={() => void renameProfile(renaming)}
            >
              Save
            </button>
            <button type="button" className="btn btn-secondary" onClick={() => setRenaming(null)}>
              Cancel
            </button>
          </div>
        </div>
      )}

      {profiles.length > 0 && (
        <div className="section">
          <h2>Segment profiles (UI-12)</h2>
          <p className="subtitle">
            Median values per segment. Compare against the platform median shown under each metric.
          </p>
          <div className="table-wrap">
            <table className="data">
              <thead>
                <tr>
                  <th>Segment</th>
                  <th>Customers</th>
                  <th>Recency (days)</th>
                  <th>Frequency</th>
                  <th>Monetary value</th>
                  <th>Avg order value</th>
                  <th>Tenure (days)</th>
                </tr>
              </thead>
              <tbody>
                {profiles.map((profile) => (
                  <tr key={profile.cluster_id}>
                    <td>
                      <strong>{profile.descriptive_label}</strong>
                      <div className="hint">cluster #{profile.cluster_id}</div>
                    </td>
                    <td>
                      {profile.count} ({(profile.share * 100).toFixed(1)}%)
                    </td>
                    <MetricCell profile={profile} feature="recency_days" />
                    <MetricCell profile={profile} feature="frequency" />
                    <MetricCell profile={profile} feature="monetary_value" />
                    <MetricCell profile={profile} feature="avg_order_value" />
                    <MetricCell profile={profile} feature="tenure_days" />
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="hint">
            Segments describe groups of historical behavior; they do not diagnose why individual
            customers leave. Validate labels with your own merchandising knowledge.
          </p>
        </div>
      )}
    </div>
  );
}

function MetricCell({ profile, feature }: { profile: Profile; feature: string }) {
  const record = (profile as unknown as Record<string, unknown>)[feature];
  const stats = (record ?? {}) as { median?: number | null; mean?: number | null };
  return (
    <td>
      {stats.median ?? "—"}
      {stats.mean !== null && stats.mean !== undefined && (
        <span className="hint"> (mean {stats.mean})</span>
      )}
    </td>
  );
}

function LoadingLine() {
  return (
    <div className="card section">
      <p className="subtitle">Loading profiles…</p>
    </div>
  );
}
