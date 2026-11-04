"use client";

// Shared page-state components (UI-14): loading, empty, error, and
// permission-denied placeholders used by every screen.

import { ApiError } from "../lib/api";

export function Loading({ label = "Loading…" }: { label?: string }) {
  return (
    <div className="page-state" role="status">
      <div className="spinner" aria-hidden="true" />
      <span>{label}</span>
    </div>
  );
}

export function Empty({ title, hint }: { title: string; hint?: string }) {
  return (
    <div className="page-state empty">
      <h2>{title}</h2>
      {hint && <p className="subtitle">{hint}</p>}
    </div>
  );
}

export function ErrorState({
  error,
  onRetry,
}: {
  error: ApiError | null;
  onRetry?: () => void;
}) {
  if (!error) return null;
  const denied = error.status === 403 || error.code === "PERMISSION_DENIED";
  return (
    <div className="page-state error-state" role="alert">
      <h2>{denied ? "Permission denied" : "Something went wrong"}</h2>
      <p className="subtitle">{error.message}</p>
      {error.status === 0 && (
        <p className="subtitle">Check that the backend URL is reachable (NEXT_PUBLIC_API_URL).</p>
      )}
      {onRetry && (
        <button type="button" className="btn btn-secondary" onClick={onRetry}>
          Try again
        </button>
      )}
    </div>
  );
}

export function PageStates({
  loading,
  error,
  empty,
  children,
  onRetry,
  emptyHint,
}: {
  loading: boolean;
  error: ApiError | null;
  empty: boolean;
  children: React.ReactNode;
  onRetry?: () => void;
  emptyHint?: string;
}) {
  if (loading) return <Loading />;
  if (error) return <ErrorState error={error} onRetry={onRetry} />;
  if (empty) return <Empty title="Nothing here yet" hint={emptyHint} />;
  return <>{children}</>;
}
