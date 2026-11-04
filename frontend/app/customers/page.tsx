"use client";

// Churn-risk customer table (UI-09): filter by risk band, order by
// probability, paginate; pick which model run to view.

import Link from "next/link";
import { useEffect, useState } from "react";

import { useApiResource, useAuthGuard, BAND_COLORS } from "../../lib/hooks";
import { PageStates } from "../../components/states";

type ModelRun = {
  id: string;
  status: string;
  train_cutoff_date: string | null;
  model_type: string;
};

type Score = {
  customer_id: string;
  churn_probability: number;
  risk_band: string;
  cutoff_date: string | null;
};

type ScoreList = { items: Score[]; total: number; pages: number; page: number };

export default function CustomersPage() {
  useAuthGuard();
  const [page, setPage] = useState(1);
  const [band, setBand] = useState("");
  const [order, setOrder] = useState("desc");
  const PAGE_SIZE = 25;

  const runs = useApiResource<{ items: ModelRun[] }>("/api/v1/churn/model-runs?page=1&page_size=50", []);
  const succeededRuns = runs.data?.items.filter((run) => run.status === "succeeded") ?? [];
  const [selectedRun, setSelectedRun] = useState<string>("");

  useEffect(() => {
    if (!selectedRun && succeededRuns.length > 0) {
      setSelectedRun(succeededRuns[0].id);
    }
  }, [selectedRun, succeededRuns]);

  const params = new URLSearchParams();
  if (selectedRun) params.set("model_run_id", selectedRun);
  if (band) params.set("risk_band", band);
  params.set("order", order);
  params.set("page", String(page));
  params.set("page_size", String(PAGE_SIZE));

  const scores = useApiResource<ScoreList>(`/api/v1/churn/scores?${params.toString()}`, [
    selectedRun,
    band,
    order,
    page,
  ]);

  return (
    <>
      <h1>Churn risks</h1>
      <p className="subtitle">
        Customers ranked by predicted churn probability from the selected model run. Bands are
        configurable thresholds over estimated probabilities — associations, not causes.
      </p>

      {succeededRuns.length === 0 && !runs.loading && !runs.error ? (
        <div className="card">
          <h2>No completed model runs yet</h2>
          <p className="subtitle">
            Train a churn model on a validated dataset version first — see{" "}
            <Link href="/datasets">Datasets</Link>.
          </p>
        </div>
      ) : (
        <>
          <div className="field-row" style={{ maxWidth: "38rem" }}>
            <label className="field">
              <span className="field-label">Model run</span>
              <select
                className="input"
                value={selectedRun}
                onChange={(e) => {
                  setSelectedRun(e.target.value);
                  setPage(1);
                }}
              >
                {succeededRuns.map((run) => (
                  <option key={run.id} value={run.id}>
                    {run.id.slice(0, 8)}… ({run.model_type}){" "}
                    {run.train_cutoff_date ? `cutoff ${run.train_cutoff_date}` : ""}
                  </option>
                ))}
              </select>
            </label>
            <label className="field">
              <span className="field-label">Risk band</span>
              <select
                className="input"
                value={band}
                onChange={(e) => {
                  setBand(e.target.value);
                  setPage(1);
                }}
              >
                <option value="">all bands</option>
                <option value="low">low</option>
                <option value="medium">medium</option>
                <option value="high">high</option>
              </select>
            </label>
            <label className="field">
              <span className="field-label">Order</span>
              <select
                className="input"
                value={order}
                onChange={(e) => {
                  setOrder(e.target.value);
                  setPage(1);
                }}
              >
                <option value="desc">highest risk first</option>
                <option value="asc">lowest risk first</option>
              </select>
            </label>
          </div>

          <div className="section">
            <PageStates
              loading={scores.loading || runs.loading}
              error={scores.error ?? runs.error}
              empty={!!scores.data && scores.data.total === 0}
              emptyHint="No scored customers match these filters."
              onRetry={scores.reload}
            >
              {scores.data && scores.data.items.length > 0 && (
                <>
                  <div className="table-wrap">
                    <table className="data">
                      <thead>
                        <tr>
                          <th>Customer</th>
                          <th>Churn probability</th>
                          <th>Risk band</th>
                          <th></th>
                        </tr>
                      </thead>
                      <tbody>
                        {scores.data.items.map((score) => (
                          <tr key={score.customer_id}>
                            <td>{score.customer_id}</td>
                            <td>{(score.churn_probability * 100).toFixed(1)}%</td>
                            <td>
                              <span className={`badge ${BAND_COLORS[score.risk_band] ?? ""}`}>
                                {score.risk_band}
                              </span>
                            </td>
                            <td>
                              <Link
                                className="btn btn-secondary btn-small"
                                href={`/customers/${encodeURIComponent(score.customer_id)}`}
                              >
                                Details
                              </Link>
                            </td>
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
                      Page {scores.data.page} of {scores.data.pages || 1} ({scores.data.total}{" "}
                      customers)
                    </span>
                    <button
                      type="button"
                      className="btn btn-secondary btn-small"
                      disabled={page >= scores.data.pages}
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
      )}
    </>
  );
}
