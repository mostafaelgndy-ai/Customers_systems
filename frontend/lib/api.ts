"use client";

// Thin client for the CustomerIQ API (Phase 7, UI-01/UI-02).
// Tokens + active organization persist in localStorage; every request
// carries the Bearer token and the X-Organization-ID header the backend
// requires for tenant scoping.

export const API_URL =
  process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

const TOKEN_KEY = "customeriq.tokens";
const ORG_KEY = "customeriq.org";

export type Tokens = { access_token?: string; refresh_token?: string };

export type Org = { id: string; name: string; role?: string };

export function loadTokens(): Tokens {
  if (typeof window === "undefined") return {};
  try {
    return JSON.parse(window.localStorage.getItem(TOKEN_KEY) || "{}") as Tokens;
  } catch {
    return {};
  }
}

export function saveTokens(tokens: Tokens) {
  window.localStorage.setItem(TOKEN_KEY, JSON.stringify(tokens));
}

export function clearTokens() {
  window.localStorage.removeItem(TOKEN_KEY);
  window.localStorage.removeItem(ORG_KEY);
}

export function loadActiveOrg(): string {
  if (typeof window === "undefined") return "";
  return window.localStorage.getItem(ORG_KEY) || "";
}

export function saveActiveOrg(orgId: string) {
  window.localStorage.setItem(ORG_KEY, orgId);
}

export class ApiError extends Error {
  status: number;
  code: string;

  constructor(status: number, code: string, message: string) {
    super(message);
    this.status = status;
    this.code = code;
  }
}

type RequestOptions = {
  method?: string;
  body?: unknown;
  form?: FormData;
  auth?: boolean;
};

async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { method = "GET", body, form, auth = true } = options;
  const headers: Record<string, string> = {};
  if (auth) {
    const token = loadTokens().access_token;
    if (token) headers["Authorization"] = `Bearer ${token}`;
    const org = loadActiveOrg();
    if (org) headers["X-Organization-ID"] = org;
  }

  let response: Response;
  try {
    response = await fetch(`${API_URL}${path}`, {
      method,
      headers: form ? headers : { ...headers, "Content-Type": "application/json" },
      body: form ?? (body !== undefined ? JSON.stringify(body) : undefined),
    });
  } catch {
    throw new ApiError(0, "NETWORK_ERROR", "API is unreachable. Is the backend running?");
  }

  if (response.status === 204) return undefined as T;

  const text = await response.text();
  let payload: unknown = undefined;
  try {
    payload = text ? JSON.parse(text) : undefined;
  } catch {
    payload = undefined;
  }

  if (!response.ok) {
    const err = (payload as { error?: { code?: string; message?: string } })?.error;
    throw new ApiError(
      response.status,
      err?.code || `HTTP_${response.status}`,
      err?.message || response.statusText || "Request failed"
    );
  }
  return payload as T;
}

export const api = {
  get: <T,>(path: string) => request<T>(path, { method: "GET" }),
  post: <T,>(path: string, body?: unknown) => request<T>(path, { method: "POST", body }),
  patch: <T,>(path: string, body?: unknown) => request<T>(path, { method: "PATCH", body }),
  upload: <T,>(path: string, form: FormData) => request<T>(path, { method: "POST", form }),
  download: async (path: string): Promise<Blob> => {
    const response = await fetch(`${API_URL}${path}`, {
      headers: {
        Authorization: `Bearer ${loadTokens().access_token}`,
        "X-Organization-ID": loadActiveOrg(),
      },
    });
    if (!response.ok) {
      throw new ApiError(response.status, "DOWNLOAD_FAILED", "Export download failed.");
    }
    return response.blob();
  },
};

// --- auth -----------------------------------------------------------------

export type TokenPair = { access_token: string; refresh_token: string };

export async function login(email: string, password: string): Promise<TokenPair> {
  return api.post<TokenPair>("/api/v1/auth/login", { email, password });
}

export async function register(
  email: string,
  password: string,
  fullName: string,
  organizationName: string
): Promise<TokenPair> {
  return api.post<TokenPair>("/api/v1/auth/register", {
    email,
    password,
    full_name: fullName,
    organization_name: organizationName,
  });
}

export type Me = {
  user: { id: string; email: string; full_name: string };
  organizations: Org[];
};

export async function me(): Promise<Me> {
  return api.get<Me>("/api/v1/auth/me");
}
