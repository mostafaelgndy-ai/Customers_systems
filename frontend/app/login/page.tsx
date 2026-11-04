"use client";

// Sign-in / registration (UI-02). Successful auth selects the user's
// first organization and lands on the overview dashboard.

import { useState } from "react";
import { useRouter } from "next/navigation";

import {
  login,
  register,
  saveTokens,
  saveActiveOrg,
  me,
  ApiError,
  type TokenPair,
} from "../../lib/api";

export default function LoginPage() {
  const router = useRouter();
  const [mode, setMode] = useState<"signin" | "register">("signin");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [fullName, setFullName] = useState("");
  const [organizationName, setOrganizationName] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function finish(tokens: TokenPair) {
    saveTokens(tokens);
    try {
      const profile = await me();
      if (profile.organizations.length > 0) {
        saveActiveOrg(profile.organizations[0].id);
      }
    } catch {
      // token refresh edge: still land on overview; the guard re-checks.
    }
    router.replace("/");
  }

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    setError(null);
    setBusy(true);
    try {
      const tokens =
        mode === "signin"
          ? await login(email, password)
          : await register(email, password, fullName, organizationName);
      await finish(tokens);
    } catch (err) {
      setError(
        err instanceof ApiError
          ? err.message
          : "Could not sign in. Check the backend connection."
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="auth-wrap">
      <div className="card auth-card">
        <h1 className="auth-title">CustomerIQ</h1>
        <p className="subtitle">
          Churn prediction &amp; customer segmentation for your store data.
        </p>
        <div className="tab-row">
          <button
            type="button"
            className={mode === "signin" ? "tab active" : "tab"}
            onClick={() => setMode("signin")}
          >
            Sign in
          </button>
          <button
            type="button"
            className={mode === "register" ? "tab active" : "tab"}
            onClick={() => setMode("register")}
          >
            New organization
          </button>
        </div>
        <form onSubmit={handleSubmit} className="form">
          <label className="field">
            <span className="field-label">Email</span>
            <input
              className="input"
              type="email"
              required
              value={email}
              onChange={(e) => setEmail(e.target.value)}
            />
          </label>
          {mode === "register" && (
            <div className="field-row">
              <label className="field">
                <span className="field-label">Your name</span>
                <input
                  className="input"
                  type="text"
                  required
                  value={fullName}
                  onChange={(e) => setFullName(e.target.value)}
                />
              </label>
              <label className="field">
                <span className="field-label">Organization</span>
                <input
                  className="input"
                  type="text"
                  required
                  value={organizationName}
                  onChange={(e) => setOrganizationName(e.target.value)}
                />
              </label>
            </div>
          )}
          <label className="field">
            <span className="field-label">Password</span>
            <input
              className="input"
              type="password"
              required
              minLength={8}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
          </label>
          {error && <div className="error-banner">{error}</div>}
          <button type="submit" className="btn btn-primary" disabled={busy}>
            {busy ? "Working…" : mode === "signin" ? "Sign in" : "Create organization"}
          </button>
        </form>
      </div>
    </div>
  );
}
