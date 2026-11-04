"use client";

// Customer detail (UI-10): prediction, top signals, and (when available)
// the segment assignment — with explicit uncertainty/causality framing
// and traceability back to the producing runs.

import Link from "next/link";
import { useParams } from "next/navigation";
import { useState } from "react";

import { formatDate, useAuthGuard, useApiResource, BAND_COLORS } from "../../../lib/hooks";
import { PageStates } from "../../../components/states";

type Signal = { feature?: string; direction?: string; impact?: number };

type CustomerDetail = {
  customer_id: string;
  dataset_version_id: string;
  data_version: string;
  churn: {
    model_run_id: string;
    churn_probability: number;
    risk_band: string;
    cutoff_date: string | null;
    explanation: {
      top_signals?: Signal[];
      note?: string;
      baseline?: number;
    } | null;
  } | null;
  segment: {
    segmentation_run_id: string;
    cluster_id: number;
    segment_label: string;
    cutoff_date: string | null;
  } | null;
  note: string;
};

type ModelRun = { id: string; status: string; dataset_version_id: string; train_cutoff_date: string | null };

export default function CustomerDetailPage() {
  useAuthGuard();
  const params = useParams<{ customerId: string }>();
  const customerId = decodeURIComponent(params?.customerId ?? "");
  const [versionOverride, setVersionOverride] = useState<string>("");

  const runs = useApiResource<{ items: ModelRun[]; total: number }>(
    "/api/v1/churn/model-runs?page=1&page_size=50",
    []
  );
  const succeededRuns = runs.data?.items.filter((run) => run.status === "succeeded") ?? [];

  const versionId = versionOverride || succeededRuns[0]?.dataset_version_id || "";

  const detail = useApiResource<CustomerDetail>(
    versionId
      ? `/api/v1/customers/${encodeURIComponent(customerId)}?dataset_version_id=${versionId}`
      : "",
    [customerId, versionId]
  );

  return (
    <>
      <div className="btn-row" style={{ justifyContent: "space-between" }}>
        <div>
          <h1>Customer {customerId}</h1>
          <p className="subtitle">Prediction, signals, and segment for one dataset version.</p>
        </div>
        <div className="btn-row">
          <label className="field">
            <span className="field-label">Data version</span>
            <select
              className="input"
              value={versionId}
              onChange={(e) => setVersionOverride(e.target.value)}
            >
              {succeededRuns.map((run) => (
                <option key={run.id} value={run.dataset_version_id}>
                  version {run.dataset_version_id.slice(0, 8)}… (run {run.id.slice(0, 8)}…)
                </option>
              ))}
            </select>
          </label>
          <Link href="/customers" className="btn btn-secondary btn-small">
            ← Back to risks
          </Link>
        </div>
      </div>

      <PageStates
        loading={detail.loading || runs.loading}
        error={detail.error ?? runs.error}
        empty={!!detail.data && detail.data.data_version === "no_data"}
        emptyHint="This customer has no churn score or segment assignment on the selected version yet."
        onRetry={detail.reload}
      >
        {detail.data && detail.data.data_version !== "no_data" && (
          <div className="split section">
            <div className="card">
              <h2>Churn prediction</h2>
              {detail.data.churn ? (
                <>
                  <div className="stats-row" style={{ marginBottom: "0.75rem" }}>
                    <div className="stat">
                      <div className="stat-value">
                        {(detail.data.churn.churn_probability * 100).toFixed(1)}%
                      </div>
                      <div className="stat-label">estimated churn probability</div>
                    </div>
                    <div className="stat">
                      <div className="stat-value">
                        <span className={`badge ${BAND_COLORS[detail.data.churn.risk_band] ?? ""}`}>
                          {detail.data.churn.risk_band}
                        </span>
                      </div>
                      <div className="stat-label">risk band (threshold-based)</div>
                    </div>
                  </div>
                  <dl className="kv">
                    <dt>Model run</dt>
                    <dd>
                      <code>{detail.data.churn.model_run_id.slice(0, 8)}…</code>
                    </dd>
                    <dt>Cutoff date</dt>
                    <dd>{detail.data.churn.cutoff_date ?? "—"}</dd>
                  </dl>
                  {detail.data.churn.explanation?.top_signals &&
                    detail.data.churn.explanation.top_signals.length > 0 && (
                      <div className="section">
                        <h2>Top signals behind this score</h2>
                        {detail.data.churn.explanation.top_signals.map((signal, index) => (
                          <div className="signal-row" key={index}>
                            <span className={signal.direction === "up" ? "dir-up" : "dir-down"}>
                              {signal.direction === "up" ? "▲" : "▼"}
                            </span>
                            <span>{signal.feature}</span>
                            {typeof signal.impact === "number" && (
                              <span className="hint">impact {signal.impact.toFixed(3)}</span>
                            )}
                          </div>
                        ))}
                      </div>
                    )}
                  <p className="hint">{detail.data.note}</p>
                </>
              ) : (
                <p className="subtitle">No churn score for this customer on the selected version.</p>
              )}
            </div>

            <div className="card">
              <h2>Segment membership</h2>
              {detail.data.segment ? (
                <>
                  <div className="stats-row" style={{ marginBottom: "0.75rem" }}>
                    <div className="stat">
                      <div className="stat-value">
                        <Link
                          href={`/segments?run=${detail.data.segment.segmentation_run_id}`}
                          className="badge badge-ok"
                        >
                          {detail.data.segment.segment_label}
                        </Link>
                      </div>
                      <div className="stat-label">segment label (editable per run)</div>
                    </div>
                    <div className="stat">
                      <div className="stat-value">#{detail.data.segment.cluster_id}</div>
                      <div className="stat-label">cluster id</div>
                    </div>
                  </div>
                  <dl className="kv">
                    <dt>Segmentation run</dt>
                    <dd>
                      <code>{detail.data.segment.segmentation_run_id.slice(0, 8)}…</code>
                    </dd>
                    <dt>Cutoff date</dt>
                    <dd>{formatDate(detail.data.segment.cutoff_date)}</dd>
                  </dl>
                </>
              ) : (
                <p className="subtitle">No segment assignment on the selected version.</p>
              )}
            </div>
          </div>
        )}
      </PageStates>
    </>
  );
}
