"use client";

// Overview dashboard (UI-08): customer/stage counts and churn-risk
// distribution, using the latest succeeded churn scoring run.

import Link from "next/link";

import { useState } from "react";

import { useAuthGuard, useApiResource, BAND_COLORS, STATUS_COLORS } from "../lib/hooks";

type DatasetList = { total: number };
type ModelRunList = {
  items: Array<{
    id: string;
    model_type: string;
    status: string;
    train_cutoff_date: string;
    metrics: {
      selection?: { selected?: string; rationale?: string };
      validation?: Record<string, { pr_auc?: number; roc_auc?: number }>;
    };
  }>;
  total: number;
};
type ScoreList = {
  items: Array<{ customer_id: string; risk_band: string; churn_probability: number }>;
  total: number;
};
type SegmentRunList = {
  items: Array<{ id: string; status: string; selected_k: number | null }>;
  total: number;
};

type Bands = Record<"low" | "medium" | "high", number>;

export default function OverviewPage() {
  useAuthGuard();

  const [page, setPage] = useState(1);

  const datasets = useApiResource<DatasetList>("/api/v1/datasets", []);
  const modelRuns = useApiResource<ModelRunList>(
    "/api/v1/churn/model-runs?page=1&page_size=50",
    []
  );
  const segmentRuns = useApiResource<SegmentRunList>(
    "/api/v1/segmentation/runs?page=1&page_size=50",
    []
  );

  const succeededRuns = modelRuns.data?.items.filter((r) => r.status === "succeeded") ?? [];
  const latestRun = succeededRuns[0];

  const { data: scores, loading } = useApiResource<ScoreList>(
    latestRun ? `/api/v1/churn/scores?model_run_id=${latestRun.id}&page=${page}&page_size=200&order=desc` : "",
    [latestRun?.id, page]
  );

  const selected = latestRun?.metrics?.selection?.selected;
  const validation = latestRun?.metrics?.validation ?? {};

  function bands(items: ScoreList["items"] | undefined): Bands {
    const result: Bands = { low: 0, medium: 0, high: 0 };
    for (const item of items ?? []) {
      if (item.risk_band === "low") result.low += 1;
      else if (item.risk_band === "medium") result.medium += 1;
      else result.high += 1;
    }
    return result;
  }
  const bandCounts = bands(scores?.items);
  const scoredTotal = scores?.total ?? 0;

  return (
    <>
      <h1>Overview</h1>
      <p className="subtitle">Current state of your customer intelligence workspace.</p>

      <div className="stats-row">
        <div className="stat">
          <div className="stat-value">{datasets.data?.total ?? 0}</div>
          <div className="stat-label">Datasets</div>
        </div>
        <div className="stat">
          <div className="stat-value">{modelRuns.data?.total ?? 0}</div>
          <div className="stat-label">Model runs</div>
        </div>
        <div className="stat">
          <div className="stat-value">{segmentRuns.data?.total ?? 0}</div>
          <div className="stat-label">Segmentation runs</div>
        </div>
        <div className="stat">
          <div className="stat-value">{scoredTotal || "—"}</div>
          <div className="stat-label">Scored customers</div>
        </div>
      </div>

      {latestRun && !loading && scoredTotal > 0 ? (
        <div className="card section">
          <h2>Churn-risk distribution</h2>
          <p className="subtitle">
            Latest scoring: model <strong>{selected}</strong> from run{" "}
            {latestRun.id.slice(0, 8)}… at cutoff {latestRun.train_cutoff_date}. Bands are
            configurable thresholds, not fixed business truth.
          </p>
          {(Object.keys(bandCounts) as Array<keyof Bands>).map((band) => {
            const count = bandCounts[band];
            const share = scoredTotal ? count / scoredTotal : 0;
            return (
              <div className="bar-wrap" key={band}>
                <span className={`badge ${BAND_COLORS[band]}`}>{band}</span>
                <div className="bar-track">
                  <div className={`bar-fill bar-${band}`} style={{ width: `${share * 100}%` }} />
                </div>
                <span>{count}</span>
              </div>
            );
          })}
          {scores?.total && scores.total > 200 * page ? (
            <p className="hint">Distribution based on the first {page * 200} scored customers.</p>
          ) : null}
          <div className="btn-row" style={{ marginTop: "1rem" }}>
            {page > 1 && (
              <button type="button" className="btn btn-secondary btn-small" onClick={() => setPage(page - 1)}>
                Previous page
              </button>
            )}
            {scores && scores.total > page * 200 && (
              <button type="button" className="btn btn-secondary btn-small" onClick={() => setPage(page + 1)}>
                Page further ({scores.total - page * 200} more)
              </button>
            )}
          </div>
          <p className="hint">
            Probabilities describe observed behavior patterns — they are associations in historical
            data, not causes of churn.
          </p>
        </div>
      ) : (
        <div className="card section">
          <h2>Churn-risk distribution</h2>
          <p className="subtitle">
            No scoring results yet. Upload a dataset and train a model to unlock this view.{" "}
            <Link href="/datasets">Start with data upload →</Link>
          </p>
        </div>
      )}

      {validation && Object.keys(validation).length > 0 && (
        <div className="card section">
          <h2>Validation metrics (latest run)</h2>
          <div className="table-wrap">
            <table className="data">
              <thead>
                <tr>
                  <th>Model</th>
                  <th>PR-AUC</th>
                  <th>ROC-AUC</th>
                </tr>
              </thead>
              <tbody>
                {Object.entries(validation).map(([model, metrics]) => (
                  <tr key={model}>
                    <td>{model}</td>
                    <td>{metrics.pr_auc?.toFixed(3) ?? "—"}</td>
                    <td>{metrics.roc_auc?.toFixed(3) ?? "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </>
  );
}
