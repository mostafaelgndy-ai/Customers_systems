"use client";

// Overview dashboard (UI-08): customer/stage counts and churn-risk
// distribution, using the latest succeeded churn scoring run.

import Link from "next/link";

import { useEffect, useState } from "react";

import { useAuthGuard, useApiResource, BAND_COLORS, STATUS_COLORS } from "../lib/hooks";
import { api } from "../lib/api";

type DatasetList = { total: number };
type ModelRunList = {
  items: Array<{
    id: string;
    model_type: string;
    status: string;
    train_cutoff_date: string;
    metrics: {
      selection?: { selected_model?: string; rationale?: string };
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

// Aggregates risk bands over ALL scored customers, not just one 200-row
// page (bounded loop: at most 10 pages = 2000 customers), so the bars
// always reflect the full run.
function useBandAggregate(modelRunId: string | undefined) {
  const [bands, setBands] = useState<Bands | null>(null);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!modelRunId) {
      setBands(null);
      setTotal(0);
      setLoading(false);
      return;
    }
    let cancelled = false;
    setLoading(true);
    (async () => {
      const result: Bands = { low: 0, medium: 0, high: 0 };
      let counted = 0;
      let runningTotal = 0;
      const MAX_PAGES = 10;
      for (let page = 1; page <= MAX_PAGES; page++) {
        const scores = await api.get<ScoreList>(
          `/api/v1/churn/scores?model_run_id=${modelRunId}&page=${page}&page_size=200&order=desc`
        );
        runningTotal = scores.total;
        for (const item of scores.items) {
          if (item.risk_band === "low") result.low += 1;
          else if (item.risk_band === "medium") result.medium += 1;
          else result.high += 1;
        }
        counted += scores.items.length;
        if (counted >= scores.total || scores.items.length === 0) break;
      }
      if (cancelled) return;
      setBands(result);
      setTotal(runningTotal);
      setLoading(false);
    })().catch(() => {
      if (!cancelled) {
        setBands(null);
        setTotal(0);
        setLoading(false);
      }
    });
    return () => {
      cancelled = true;
    };
  }, [modelRunId]);

  return { bands, total, loading };
}

export default function OverviewPage() {
  useAuthGuard();

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
  const { bands: bandCounts, total: scoredTotal, loading } = useBandAggregate(latestRun?.id);

  const selected = latestRun?.metrics?.selection?.selected_model;
  const rationale = latestRun?.metrics?.selection?.rationale;
  const validation = latestRun?.metrics?.validation ?? {};

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
            Latest scoring: model <strong>{selected ?? "—"}</strong> from run{" "}
            {latestRun.id.slice(0, 8)}… at cutoff {latestRun.train_cutoff_date}. Bands are
            configurable thresholds, not fixed business truth.
            {rationale ? ` Selection: ${rationale}` : ""}
          </p>
          {(Object.keys(bandCounts ?? { low: 0, medium: 0, high: 0 }) as Array<keyof Bands>).map((band) => {
            const count = (bandCounts ?? { low: 0, medium: 0, high: 0 })[band];
            const share = scoredTotal ? count / scoredTotal : 0;
            return (
              <div className="bar-wrap" key={band}>
                <span className={`badge ${BAND_COLORS[band]}`}>{band}</span>
                <div className="bar-track" role="img" aria-label={`${band} risk: ${count} of ${scoredTotal} customers (${Math.round(share * 100)}%)`}>
                  <div className={`bar-fill bar-${band}`} style={{ width: `${share * 100}%` }} />
                </div>
                <span>{count}</span>
              </div>
            );
          })}
          {bandCounts && scoredTotal > 2000 ? (
            <p className="hint">
              Distribution computed from the first 2000 scored customers of this run.
            </p>
          ) : null}
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
