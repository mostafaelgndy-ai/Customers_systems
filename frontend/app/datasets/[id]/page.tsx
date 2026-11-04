"use client";

// Dataset workspace (UI-03 upload + templates, UI-04 mapping, UI-05
// validation report). One page covers the full data-preparation flow:
// upload customers/orders CSVs, review/adjust the suggested column
// mapping, bind files into an immutable version, and watch validation.

import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

import { API_URL, api, ApiError } from "../../../lib/api";
import { formatDate, STATUS_COLORS, useAuthGuard } from "../../../lib/hooks";
import { ErrorState, Loading } from "../../../components/states";

type DatasetFile = {
  id: string;
  kind: "customers" | "orders";
  original_filename: string;
  size_bytes: number;
  status: string;
  headers: string[];
  mapping: Record<string, string> | null;
  created_at: string;
};

type VersionSummary = { id: string; version_number: number; status: string };

type VersionDetail = {
  id: string;
  version_number: number;
  status: string;
  created_at: string;
  error_summary: string | null;
  report?: {
    status?: string;
    summary?: Record<string, Record<string, unknown>>;
    issue_counts?: { errors: number; warnings: number };
    issues?: ValidationIssue[];
    issues_truncated?: boolean;
  };
  job_id: string | null;
  job_status?: string;
  progress?: number;
};

type DatasetDetail = {
  id: string;
  name: string;
  description?: string | null;
  files: DatasetFile[];
  versions: VersionSummary[];
};

const CANONICAL_FIELDS: Record<"customers" | "orders", { required: string[]; optional: string[] }> = {
  customers: {
    required: ["customer_id"],
    optional: ["created_at", "country", "acquisition_channel", "customer_status"],
  },
  orders: {
    required: ["order_id", "customer_id", "order_date", "order_status", "order_amount"],
    optional: ["currency", "discount_amount", "shipping_amount", "item_count"],
  },
};

const POLL_MS = 2500;

type ValidationIssue = {
  severity: string;
  code?: string;
  message?: string;
  count: number;
  example_rows?: number[];
};

export default function DatasetPage() {
  useAuthGuard();
  const params = useParams<{ id: string }>();
  const datasetId = params?.id ?? "";

  const [detail, setDetail] = useState<DatasetDetail | null>(null);
  const [loadError, setLoadError] = useState<ApiError | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);

  const [uploadingKind, setUploadingKind] = useState<string | null>(null);
  const [mappingFor, setMappingFor] = useState<string | null>(null);
  const [draftMapping, setDraftMapping] = useState<Record<string, string>>({});
  const [savedMappingFor, setSavedMappingFor] = useState<string | null>(null);
  const [selectedCustomers, setSelectedCustomers] = useState<string>("");
  const [selectedOrders, setSelectedOrders] = useState<string>("");
  const [creatingVersion, setCreatingVersion] = useState(false);

  const [openVersionId, setOpenVersionId] = useState<string>("");
  const [versionDetail, setVersionDetail] = useState<VersionDetail | null>(null);
  const [versionLoading, setVersionLoading] = useState(false);

  const loadDataset = useCallback(async () => {
    try {
      const data = await api.get<DatasetDetail>(`/api/v1/datasets/${datasetId}`);
      setDetail(data);
      setLoadError(null);
    } catch (err) {
      setLoadError(err instanceof ApiError ? err : new ApiError(0, "NETWORK_ERROR", "Failed to load dataset."));
    }
  }, [datasetId]);

  useEffect(() => {
    if (datasetId) void loadDataset();
  }, [datasetId, loadDataset]);

  // Version polling (UI-05): re-fetch every 2.5s while a version is
  // validating; stops as soon as a terminal status arrives.
  useEffect(() => {
    if (!openVersionId) return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | null = null;

    async function poll() {
      try {
        const data = await api.get<VersionDetail>(`/api/v1/datasets/${datasetId}/versions/${openVersionId}`);
        if (cancelled) return;
        setVersionDetail(data);
        if (data.status === "validating") {
          timer = setTimeout(poll, POLL_MS);
        }
      } catch {
        if (!cancelled) timer = setTimeout(poll, POLL_MS); // transient errors: keep trying
      }
    }

    void poll();
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, [openVersionId, datasetId]);

  async function handleUpload(kind: "customers" | "orders", file: File | null) {
    if (!file) {
      setActionError("Choose a CSV file first.");
      return;
    }
   setUploadingKind(kind);
    setActionError(null);
    try {
      const form = new FormData();
      form.append("kind", kind);
      form.append("file", file);
      const response = await api.upload<{
        file: DatasetFile;
        suggested_mapping: Record<string, string>;
      }>(`/api/v1/datasets/${datasetId}/files`, form);
      await loadDataset();
      // Jump straight into mapping review with the suggested mapping.
      setMappingFor(response.file.id);
      setDraftMapping(response.suggested_mapping ?? {});
      setSavedMappingFor(null);
    } catch (err) {
      setActionError(
        err instanceof ApiError
          ? err.message
          : "The upload failed. Check the file and backend connection."
      );
    } finally {
      setUploadingKind(null);
    }
  }

  async function saveMapping(file: DatasetFile) {
    setActionError(null);
    try {
      const response = await api.patch<{ ready_for_version: boolean }>(
        `/api/v1/datasets/${datasetId}/files/${file.id}`,
        { mapping: draftMapping }
      );
      await loadDataset();
      setMappingFor(null);
      setSavedMappingFor(file.id);
      if (!response.ready_for_version) {
        setActionError(
          "Saved — but required columns are still unmapped, so this file cannot join a version yet."
        );
      }
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : "Could not save the mapping.");
    }
  }

  async function createVersion() {
    if (!selectedCustomers || !selectedOrders) {
      setActionError("Select one mapped customers file and one mapped orders file.");
      return;
    }
    setCreatingVersion(true);
    setActionError(null);
    try {
      const response = await api.post<{ version: { id: string; status: string }; job_id: string }>(
        `/api/v1/datasets/${datasetId}/versions`,
        { customers_file_id: selectedCustomers, orders_file_id: selectedOrders }
      );
      await loadDataset();
      openVersion(response.version.id);
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : "Could not create the version.");
    } finally {
      setCreatingVersion(false);
    }
  }

  function openVersion(versionId: string) {
    setOpenVersionId(versionId);
    setVersionDetail(null);
    setVersionLoading(true);
    api
      .get<VersionDetail>(`/api/v1/datasets/${datasetId}/versions/${versionId}`)
      .then((data) => {
        setVersionDetail(data);
        setVersionLoading(false);
      })
      .catch(() => {
        setVersionLoading(false);
      });
  }

  async function retryVersion(versionId: string) {
    setActionError(null);
    try {
      await api.post(`/api/v1/datasets/${datasetId}/versions/${versionId}/retry`);
      openVersion(versionId);
      await loadDataset();
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : "Could not retry this version.");
    }
  }

  if (loadError) {
    return <ErrorState error={loadError} onRetry={loadDataset} />;
  }
  if (!detail) {
    return <Loading label="Loading dataset…" />;
  }

  // Filter mapped (version-ready) files for the version-creation selects.
  const readyCustomers = detail.files.filter((f) => f.kind === "customers" && isReady(f));
  const readyOrders = detail.files.filter((f) => f.kind === "orders" && isReady(f));

  return (
    <>
      <div className="btn-row" style={{ justifyContent: "space-between" }}>
        <div>
          <h1>{detail.name}</h1>
          <p className="subtitle">Upload files, map columns, and create validated versions.</p>
        </div>
        <Link href="/datasets" className="btn btn-secondary btn-small">
          ← All datasets
        </Link>
      </div>

      {actionError && <div className="error-banner">{actionError}</div>}

      {/* --- UI-03: upload + templates ------------------------------------- */}
      <div className="card section">
        <div className="btn-row" style={{ justifyContent: "space-between" }}>
          <h2>Files</h2>
          <div className="btn-row">
            <a
              className="btn btn-secondary btn-small"
              href={`${API_URL}/api/v1/datasets/templates/customers`}
              download
            >
              customers_template.csv
            </a>
            <a
              className="btn btn-secondary btn-small"
              href={`${API_URL}/api/v1/datasets/templates/orders`}
              download
            >
              orders_template.csv
            </a>
          </div>
        </div>
        <p className="subtitle">
          Templates show the canonical columns the platform understands. Your CSV can use different
          header names — you will map them in the next step.
        </p>

        {detail.files.length === 0 ? (
          <p className="subtitle">No files yet — upload your customers and orders CSV below.</p>
        ) : (
          <div className="table-wrap">
            <table className="data">
              <thead>
                <tr>
                  <th>Kind</th>
                  <th>File</th>
                  <th>Size</th>
                  <th>Mapping</th>
                  <th>Uploaded</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {detail.files.map((file) => (
                  <tr key={file.id}>
                    <td>
                      <span className="badge badge-neutral">{file.kind}</span>
                    </td>
                    <td>{file.original_filename}</td>
                    <td>{Math.round(file.size_bytes / 1024)} KB</td>
                    <td>
                      {file.mapping ? (
                        <span className="badge badge-ok">
                          {Object.keys(file.mapping).length} columns mapped
                        </span>
                      ) : (
                        <span className="badge badge-progress">not mapped</span>
                      )}
                    </td>
                    <td>{formatDate(file.created_at)}</td>
                    <td>
                      <button
                        type="button"
                        className="btn btn-secondary btn-small"
                        onClick={() => {
                          setMappingFor(file.id);
                          setDraftMapping(file.mapping ?? {});
                          setSavedMappingFor(null);
                        }}
                      >
                        Edit mapping
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        <div className="field-row section" style={{ marginTop: "1rem" }}>
          {(["customers", "orders"] as const).map((kind) => (
            <label className="field" key={kind}>
              <span className="field-label">
                Upload {kind} CSV {uploadingKind === kind ? "(uploading…)" : ""}
              </span>
              <input
                className="input"
                type="file"
                accept=".csv,text/csv"
                disabled={uploadingKind !== null}
                onChange={(e) => void handleUpload(kind, e.target.files?.[0] ?? null)}
              />
            </label>
          ))}
        </div>
      </div>

      {/* --- UI-04: column mapping ------------------------------------------ */}
      {detail.files
        .filter((file) => mappingFor === file.id)
        .map((file) => {
          const schema = CANONICAL_FIELDS[file.kind];
          return (
            <div className="card section" key={file.id}>
              <h2>
                Map columns — {file.original_filename} <span className="badge badge-neutral">{file.kind}</span>
              </h2>
              <p className="subtitle">
                Each canonical field reads from one of your CSV columns. Required fields must be set
                before the file can join a dataset version.
              </p>
              <div className="table-wrap">
                <table className="data">
                  <thead>
                    <tr>
                      <th>Canonical field</th>
                      <th></th>
                      <th>Source column in your CSV</th>
                    </tr>
                  </thead>
                  <tbody>
                    {[...schema.required, ...schema.optional].map((canonical) => {
                      const required = schema.required.includes(canonical);
                      return (
                        <tr key={canonical}>
                          <td>
                            {canonical}{" "}
                            {required && <span className="badge badge-high">required</span>}
                          </td>
                          <td>
                            <span className="hint">from →</span>
                          </td>
                          <td>
                            <select
                              className="input"
                              value={draftMapping[canonical] ?? ""}
                              onChange={(e) =>
                                setDraftMapping({ ...draftMapping, [canonical]: e.target.value })
                              }
                            >
                              <option value="">— not mapped —</option>
                              {(file.headers ?? []).map((header) => (
                                <option key={header} value={header}>
                                  {header}
                                </option>
                              ))}
                            </select>
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
              <div className="btn-row" style={{ marginTop: "0.75rem" }}>
                <button type="button" className="btn btn-primary" onClick={() => void saveMapping(file)}>
                  Save mapping
                </button>
                <button type="button" className="btn btn-secondary" onClick={() => setMappingFor(null)}>
                  Close
                </button>
                {savedMappingFor === file.id && (
                  <span className="badge badge-ok">saved</span>
                )}
              </div>
            </div>
          );
        })}

      {/* --- versions + UI-05 validation ------------------------------------- */}
      <div className="card section">
        <h2>Dataset versions</h2>
        {detail.versions.length === 0 ? (
          <p className="subtitle">No versions yet — bind two mapped files into a version below.</p>
        ) : (
          <div className="table-wrap">
            <table className="data">
              <thead>
                <tr>
                  <th>Version</th>
                  <th>Status</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {detail.versions.map((version) => (
                  <tr key={version.id}>
                    <td>v{version.version_number}</td>
                    <td>
                      <span className={`badge ${STATUS_COLORS[version.status] ?? ""}`}>
                        {version.status}
                      </span>
                    </td>
                    <td>
                      <button
                        type="button"
                        className="btn btn-secondary btn-small"
                        onClick={() => openVersion(version.id)}
                      >
                        View report
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        <div className="field-row" style={{ marginTop: "1.25rem" }}>
          <label className="field">
            <span className="field-label">Customers file</span>
            <select
              className="input"
              value={selectedCustomers}
              onChange={(e) => setSelectedCustomers(e.target.value)}
            >
              <option value="">— select a mapped customers file —</option>
              {readyCustomers.map((f) => (
                  <option key={f.id} value={f.id}>
                    {f.original_filename}
                  </option>
                ))}
            </select>
          </label>
          <label className="field">
            <span className="field-label">Orders file</span>
            <select
              className="input"
              value={selectedOrders}
              onChange={(e) => setSelectedOrders(e.target.value)}
            >
              <option value="">— select a mapped orders file —</option>
              {readyOrders.map((f) => (
                  <option key={f.id} value={f.id}>
                    {f.original_filename}
                  </option>
                ))}
            </select>
          </label>
        </div>
        <div className="btn-row" style={{ marginTop: "0.75rem" }}>
          <button
            type="button"
            className="btn btn-primary"
            disabled={creatingVersion}
            onClick={() => void createVersion()}
          >
            {creatingVersion ? "Creating…" : "Create version & validate"}
          </button>
        </div>
      </div>

      {openVersionId && (
        <div className="card section">
          <h2>
            Validation report{" "}
            {versionDetail && (
              <span className={`badge ${STATUS_COLORS[versionDetail.status] ?? ""}`}>
                {versionDetail.status}
              </span>
            )}
          </h2>
          {versionLoading || !versionDetail ? (
            <Loading label="Loading report…" />
          ) : versionDetail.status === "validating" ? (
            <div className="page-state">
              <div className="spinner" aria-hidden="true" />
              <p className="subtitle">
                Validation in progress
                {typeof versionDetail.progress === "number"
                  ? ` — ${versionDetail.progress}%`
                  : ""}{" "}
                (updates every {POLL_MS / 1000}s)
              </p>
              {versionDetail.job_id && (
                <Link href="/runs" className="btn btn-secondary btn-small">
                  Track in jobs →
                </Link>
              )}
            </div>
          ) : versionDetail.status === "invalid" || versionDetail.status === "failed" ? (
            <>
              {versionDetail.error_summary && (
                <div className="error-banner">{versionDetail.error_summary}</div>
              )}
              {versionDetail.report?.issues && versionDetail.report.issues.length > 0 ? (
                <div className="table-wrap">
                  <table className="data">
                    <thead>
                      <tr>
                        <th>Severity</th>
                        <th>Issue</th>
                        <th>Rows</th>
                        <th>Example rows</th>
                      </tr>
                    </thead>
                    <tbody>
                      {versionDetail.report.issues.map((issue, index) => (
                        <tr key={index}>
                          <td>
                            <span
                              className={
                                issue.severity === "error"
                                  ? "badge badge-high"
                                  : "badge badge-progress"
                              }
                            >
                              {issue.severity}
                            </span>
                          </td>
                          <td>
                            {issue.code ? `${issue.code}: ` : ""}
                            {issue.message}
                          </td>
                          <td>{issue.count}</td>
                          <td>{(issue.example_rows ?? []).slice(0, 5).join(", ")}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                  {versionDetail.report.issues_truncated && (
                    <p className="hint">Only the most frequent issues are shown.</p>
                  )}
                </div>
              ) : (
                <p className="subtitle">No issue detail available for this version.</p>
              )}
              {versionDetail.status === "failed" && (
                <div className="btn-row">
                  <button
                    type="button"
                    className="btn btn-secondary"
                    onClick={() => void retryVersion(versionDetail.id)}
                  >
                    Retry validation
                  </button>
                </div>
              )}
            </>
          ) : versionDetail.status === "cancelled" ? (
            <p className="subtitle">
              Validation was cancelled before completion. Create a new version to try again.
            </p>
          ) : (
            <>
              <div className="info-banner">
                This version is valid and ready for analysis. Use it when creating feature, churn, or
                segmentation runs.
              </div>
              {versionDetail.report?.issue_counts &&
                versionDetail.report.issue_counts.warnings > 0 && (
                  <p className="hint">
                    {" "}
                    {versionDetail.report.issue_counts.warnings} warnings were recorded in the report.
                  </p>
                )}
              {versionDetail.report?.summary && (
                <div className="split section">
                  {Object.entries(versionDetail.report.summary).map(([kind, metrics]) => (
                    <dl className="kv card" key={kind}>
                      <dt>
                        <strong>{kind}</strong>
                      </dt>
                      {Object.entries(metrics).map(([key, value]) => (
                        <div key={key} style={{ display: "contents" }}>
                          <dt>{key.replace(/_/g, " ")}</dt>
                          <dd>{String(value)}</dd>
                        </div>
                      ))}
                    </dl>
                  ))}
                </div>
              )}
              <AnalysisConfig versionId={versionDetail.id} />
            </>
          )}
        </div>
      )}
    </>
  );
}

/** UI-06: analysis configuration for a validated version (churn training
 * + segmentation run options). Created runs are tracked on the runs page. */
function AnalysisConfig({ versionId }: { versionId: string }) {
  const [observationDays, setObservationDays] = useState(180);
  const [horizonDays, setHorizonDays] = useState(90);
  const [seed, setSeed] = useState(42);
  const [kCandidates, setKCandidates] = useState("3,4,5");
  const [busy, setBusy] = useState<"churn" | "segmentation" | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [launchError, setLaunchError] = useState<string | null>(null);

  async function launchChurn() {
    setBusy("churn");
    setLaunchError(null);
    setMessage(null);
    try {
      await api.post("/api/v1/churn/model-runs", {
        dataset_version_id: versionId,
        observation_window_days: observationDays,
        label_horizon_days: horizonDays,
        random_seed: seed,
      });
      setMessage("Churn training started — track it under Runs & jobs.");
    } catch (err) {
      setLaunchError(err instanceof ApiError ? err.message : "Could not start the churn run.");
    } finally {
      setBusy(null);
    }
  }

  async function launchSegmentation() {
    setBusy("segmentation");
    setLaunchError(null);
    setMessage(null);
    try {
      const candidates = kCandidates
        .split(",")
        .map((part) => Number.parseInt(part.trim(), 10))
        .filter((n) => Number.isFinite(n) && n > 1);
      await api.post("/api/v1/segmentation/runs", {
        dataset_version_id: versionId,
        observation_window_days: observationDays,
        k_candidates: candidates.length > 0 ? candidates : undefined,
        random_seed: seed,
      });
      setMessage("Segmentation started — track it under Runs & jobs.");
    } catch (err) {
      setLaunchError(
        err instanceof ApiError ? err.message : "Could not start the segmentation run."
      );
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="section">
      <h2 style={{ marginTop: "1.5rem" }}>Run an analysis on this version (UI-06)</h2>
      <p className="subtitle">
        Observation window = how much history the models read; label horizon = how far ahead churn
        is defined. Defaults are reasonable; larger windows need more history.
      </p>
      <div className="field-row">
        <label className="field">
          <span className="field-label">Observation window (days)</span>
          <input
            className="input"
            type="number"
            min={30}
            max={730}
            value={observationDays}
            onChange={(e) => setObservationDays(Number(e.target.value) || 180)}
          />
        </label>
        <label className="field">
          <span className="field-label">Churn label horizon (days)</span>
          <input
            className="input"
            type="number"
            min={14}
            max={365}
            value={horizonDays}
            onChange={(e) => setHorizonDays(Number(e.target.value) || 90)}
          />
        </label>
        <label className="field">
          <span className="field-label">Random seed</span>
          <input
            className="input"
            type="number"
            min={0}
            value={seed}
            onChange={(e) => setSeed(Number(e.target.value) || 42)}
          />
        </label>
        <label className="field">
          <span className="field-label">Segment k candidates (comma-separated)</span>
          <input
            className="input"
            type="text"
            value={kCandidates}
            onChange={(e) => setKCandidates(e.target.value)}
          />
        </label>
      </div>
      {message && <div className="info-banner" style={{ marginTop: "0.75rem" }}>{message}</div>}
      {launchError && (
        <div className="error-banner" style={{ marginTop: "0.75rem" }}>
          {launchError}
        </div>
      )}
      <div className="btn-row" style={{ marginTop: "0.75rem" }}>
        <button
          type="button"
          className="btn btn-primary"
          disabled={busy !== null}
          onClick={() => void launchChurn()}
        >
          {busy === "churn" ? "Starting…" : "Train churn model"}
        </button>
        <button
          type="button"
          className="btn btn-primary"
          disabled={busy !== null}
          onClick={() => void launchSegmentation()}
        >
          {busy === "segmentation" ? "Starting…" : "Run segmentation"}
        </button>
        <Link href="/runs" className="btn btn-secondary">
          Runs & jobs →
        </Link>
      </div>
    </div>
  );
}

/** A file is version-ready when every canonical required field is mapped. */
function isReady(file: DatasetFile): boolean {
  const required = CANONICAL_FIELDS[file.kind].required;
  return required.every((field) => Boolean(file.mapping?.[field]));
}
