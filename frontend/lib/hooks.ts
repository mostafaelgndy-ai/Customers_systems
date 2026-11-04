"use client";

// Auth guard hook (UI-01): every authenticated page uses this.
// Loads the profile, ensures the active organization header is set,
// and redirects to /login when the token is missing/expired (401).

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";

import { api, me, loadActiveOrg, saveActiveOrg, clearTokens, ApiError, type Me } from "./api";

export function useAuthGuard() {
  const router = useRouter();
  const [profile, setProfile] = useState<Me | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<ApiError | null>(null);

  useEffect(() => {
    let cancelled = false;
    me()
      .then((data) => {
        if (cancelled) return;
        if (!loadActiveOrg() && data.organizations.length > 0) {
          saveActiveOrg(data.organizations[0].id);
        }
        setProfile(data);
        setLoading(false);
      })
      .catch((err: ApiError) => {
        if (cancelled) return;
        if (err.status === 401) {
          clearTokens();
          router.replace("/login");
          return;
        }
        setError(err);
        setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [router]);

  return { profile, loading, error };
}

// Shared list-fetch hook with loading/empty/error states (UI-14).
export function useApiResource<T>(path: string, deps: unknown[] = []) {
  const router = useRouter();
  const [data, setData] = useState<T | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<ApiError | null>(null);
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => {
    // Empty path = resource not yet selectable; surface as "no data yet".
    if (!path) {
      setData(null);
      setLoading(false);
      return;
    }
    let cancelled = false;
    setLoading(true);
    setError(null);
    api
      .get<T>(path)
      .then((result) => {
        if (!cancelled) {
          setData(result);
          setLoading(false);
        }
      })
      .catch((err: ApiError) => {
        if (cancelled) return;
        if (err.status === 401) {
          clearTokens();
          router.replace("/login");
          return;
        }
        setError(err);
        setLoading(false);
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [path, router, reloadKey, ...deps]);

  return { data, loading, error, reload: () => setReloadKey((k) => k + 1) };
}

export function formatDate(iso: string | null | undefined): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleString();
}

export const BAND_COLORS: Record<string, string> = {
  low: "band-low",
  medium: "band-medium",
  high: "band-high",
};

export const STATUS_COLORS: Record<string, string> = {
  succeeded: "status-ok",
  failed: "status-bad",
  running: "status-progress",
  queued: "status-progress",
};
