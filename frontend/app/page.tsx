"use client";

import { useEffect, useState } from "react";

// Phase 7 builds the real dashboard; this shell only proves wiring:
// it reports API liveness/readiness from NEXT_PUBLIC_API_URL (UI-01 stub).
const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

type Health = { status: string; environment: string } | null;
type Readiness = { status: string; checks: Record<string, string> } | null;

function StatusLine({ label, value, ok }: { label: string; value: string | null; ok: boolean | null }) {
  return (
    <div className="status-line">
      <span className="status-label">{label}</span>
      <span className={"status-value " + (ok === null ? "" : ok ? "ok" : "err")}>
        {value ?? "…"}
      </span>
    </div>
  );
}

export default function Home() {
  const [health, setHealth] = useState<Health>(null);
  const [readiness, setReadiness] = useState<Readiness>(null);

  useEffect(() => {
    fetch(`${API_URL}/api/v1/health`)
      .then((r) => r.json())
      .then(setHealth)
      .catch(() => setHealth(null));
    fetch(`${API_URL}/api/v1/health/readiness`)
      .then((r) => r.json())
      .then(setReadiness)
      .catch(() => setReadiness(null));
  }, []);

  return (
    <main>
      <h1>CustomerIQ</h1>
      <p className="subtitle">Churn prediction & customer segmentation for e-commerce</p>
      <div className="status-card">
        <StatusLine label="API URL" value={API_URL} ok={true} />
        <StatusLine
          label="API liveness"
          value={health ? health.status : "unreachable"}
          ok={health ? health.status === "ok" : false}
        />
        <StatusLine
          label="API readiness"
          value={readiness ? readiness.status : "pending"}
          ok={readiness ? readiness.status === "ok" : null}
        />
        {readiness &&
          Object.entries(readiness.checks).map(([name, state]) => (
            <StatusLine key={name} label={"— " + name} value={state} ok={state === "ok"} />
          ))}
      </div>
      <p className="hint">
        Dashboard screens (upload, validation, runs, churn table, segmentation) land in
        Phase 7 — see docs/TASKS.md.
      </p>
    </main>
  );
}
