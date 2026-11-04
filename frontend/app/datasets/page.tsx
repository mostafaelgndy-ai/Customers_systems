"use client";

// Datasets list + create (UI-03 entry). Upload flow lives per dataset.

import Link from "next/link";
import { useState } from "react";

import { api } from "../../lib/api";
import { formatDate, useAuthGuard, useApiResource } from "../../lib/hooks";
import { PageStates } from "../../components/states";

type Dataset = {
  id: string;
  name: string;
  created_at: string;
};

type DatasetList = {
  items: Dataset[];
  total: number;
};

export default function DatasetsPage() {
  useAuthGuard();
  const { data, loading, error, reload } = useApiResource<DatasetList>(
    "/api/v1/datasets?page=1&page_size=50"
  );
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [createError, setCreateError] = useState<string | null>(null);

  async function handleCreate(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setCreateError(null);
    try {
      const response = await api.post<{ dataset: Dataset }>("/api/v1/datasets", { name });
      setName("");
      reload();
      window.location.href = `/datasets/${response.dataset.id}`;
    } catch {
      setCreateError("Could not create the dataset.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <h1>Datasets</h1>
      <p className="subtitle">Customers + orders CSV files; each dataset keeps versions and validation reports.</p>

      <PageStates
        loading={loading}
        error={error}
        empty={!loading && !error && !!data && data.total === 0}
        emptyHint="Create your first dataset below to start uploading CSVs."
        onRetry={reload}
      >
        {data && data.total > 0 && (
          <div className="card">
            <div className="table-wrap">
              <table className="data">
                <thead>
                  <tr>
                    <th>Name</th>
                    <th>Created</th>
                    <th></th>
                  </tr>
                </thead>
                <tbody>
                  {data.items.map((dataset) => (
                    <tr key={dataset.id}>
                      <td>
                        <Link href={`/datasets/${dataset.id}`}>{dataset.name}</Link>
                      </td>
                      <td>{formatDate(dataset.created_at)}</td>
                      <td>
                        <Link className="btn btn-secondary btn-small" href={`/datasets/${dataset.id}`}>
                          Open
                        </Link>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}
      </PageStates>

      <div className="card section">
        <h2>New dataset</h2>
        <form onSubmit={handleCreate} className="form">
          <label className="field">
            <span className="field-label">Dataset name</span>
            <input
              className="input"
              type="text"
              required
              placeholder="e.g. Winter season export"
              value={name}
              onChange={(e) => setName(e.target.value)}
            />
          </label>
          {createError && <div className="error-banner">{createError}</div>}
          <div>
            <button type="submit" className="btn btn-primary" disabled={busy}>
              {busy ? "Creating…" : "Create dataset"}
            </button>
          </div>
        </form>
      </div>
    </>
  );
}
