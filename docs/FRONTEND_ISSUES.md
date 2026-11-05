# Frontend Test Report — Phase 7 UI Audit (2026-11-05)

**Scope:** the Phase 7 dashboard at `http://localhost:3000`, against the live docker stack
(backend `:8000`, Celery worker, Postgres, Redis — all healthy).

**Method (no GUI browser available; both approaches were used):**

1. **Headless Edge** (real Chromium render + React execution) over `/login` and every
   authenticated route — checks for client-side crashes and hydration failures.
2. **API-flow replay** (`ui_flow_test.mjs`, reproducible): a Node script performs *exactly* the
   sequence of HTTP calls the UI makes, in UI order (register → overview → dataset create →
   template download → upload → suggested-mapping PATCH → version create → validation poll →
   churn train → score → customer detail → segmentation run → rename → compare → export →
   download → jobs lists) and asserts every response field the components consume.
   Synthetic dataset: 150 customers / ~960 orders over ~14 months (small fixtures are too short
   to train on — see Finding 13).

Unauthenticated renders of all routes are crash-free; login page hydrates correctly.

---

## Findings

### C1 (Critical) — Segments page breaks on every real succeeded run: `profiles` shape mismatch
- **Where:** `frontend/app/segments/page.tsx` (`Profile` type, distribution bars, profiles table, rename)
- **Evidence:** `segmentation_runs.profiles` in the DB is `{"segments": [...], "labels_editable": true}`
  (verified via psql on the succeeded run produced by the replay), so
  `GET /api/v1/segmentation/runs/{id}` returns **an object, not an array**.
  The frontend declares `profiles: Profile[] | null` and calls `profiles.map(...)` — on a real
  run this throws a React runtime error; the entire Segments tab is broken for real data.
- **Impact:** UI-11/12/15 (the core segmentation screens) unusable on real succeeded runs.
  The replay confirmed the flow silently short-circuits before rename/compare assertions.
- **Proposed fix (frontend-side, smallest):** unwrap — `const segments = run.profiles?.segments ?? []`
  and use `run.profiles?.labels_editable` for the per-profile rename affordance. Alternative is a
  backend DTO change, but the rename endpoint already persists this wrapper shape, so the frontend
  should adapt.

### C2 (Critical/High) — Customer detail "top signals" arrows wrong: direction values not handled
- **Where:** `frontend/app/customers/[customerId]/page.tsx` (signal rows: `{direction === "up" ? ▲ green-safe}`
- **Evidence:** backend scoring emits `"direction": "higher_risk" | "lower_risk"`
  (`app/domains/churn/scoring.py:184`). The UI only knows `"up"`/`"down"`, so every signal
  rendered as **▼ with the green "lower-risk" color while meaning higher risk** — inverted signal
  meaning on the most sensitive screen (customer risk explanation).
- **Impact:** CHURN-16 explanation display misleads the user on direction of risk. Approved by no test.
- **Proposed fix (frontend-side):** map `higher_risk → ▲ / dir-up`, `lower_risk → ▼ / dir-down`
  (keep the existing fallback for anything else).

### H1 (High) — Validation summary renders literal `[object Object]`
- **Where:** `frontend/app/datasets/[id]/page.tsx` (valid-version summary `dl.kv` block)
- **Evidence:** replay flagged `report.summary.orders.status_counts` and `currency_counts` are
  nested dicts, e.g. `{"completed":792,"returned":87,"refunded":84}`. The UI renders
  `String(value)` per key → users see `[object Object]` in the validation report.
- **Proposed fix:** in the summary renderer, when the value is an object render
  `key1: v1 · key2: v2`; skip empty objects.

### H2 (High) — Overview "Latest scoring: model ______" is blank
- **Where:** `frontend/app/page.tsx` (overview reads `metrics.selection.selected`)
- **Evidence:** real metric shape is `selection: { selected_model, selection_basis, rationale }`
  (psql-verified on the succeeded run). `selected` doesn't exist → the overview shows no model name.
- **Proposed fix:** read `selection.selected_model` (and optionally surface `rationale`).

### H3 (High) — CSV export download 404s in the docker stack
- **Where:** backend/compose wiring, surfaced through `frontend/app/exports/page.tsx`
- **Evidence:** replay: `POST /exports` job succeeded, `GET /exports/{id}/download → 404`.
  Root cause: `Settings.exports_dir` defaults to relative `data/exports` (container-local);
  the **worker** writes the file in its own container, the **backend** serves downloads from
  **its own** container where the file does not exist. Uploads already solve this correctly via a
  shared named volume (`uploads:/data/uploads`) — exports never got the same treatment.
- **Proposed fix (backend/compose, needs approval as it is not purely frontend):** set
  `EXPORTS_DIR=/data/exports` for backend + worker in `docker-compose.yml`, add shared volume
  `exports:/data/exports` to both services, and add the knob to `.env.example`.
  (Alternatively bind `./backend/data` shared — the named volume matches the uploads pattern better.)

### M1 (Medium) — Overview band distribution is misleading for >200 scored customers
- **Where:** `frontend/app/page.tsx`
- **Evidence/impact:** bars are computed from only the current 200-row page of scores while the
  denominator is the full run total, so shares shift as you page — a genuinely wrong distribution
  that quietly changes under the user.
- **Proposed fix:** loop-score pages (bounded, e.g. up to 10×200) to aggregate true band counts, or
  add a tiny backed aggregate endpoint (needs approval). Recommend the frontend loop for now —
  pilot datasets are well under that size.

### M2 (Medium) — Stale/absent organization id leaves every screen "Permission denied"
- **Where:** `frontend/lib/hooks.ts`
- **Evidence/impact:** `useAuthGuard` redirects only on 401. If the stored org id is stale or
  header missing while the token is valid, every org call 403s and all pages show a dead
  permission error instead of re-selecting the org (the very likely state after schema resets
  with reused browser storage, e.g. DB re-clone during testing — happened during this audit).
- **Proposed fix:** in `useApiResource`/`useAuthGuard`, on 403 `PERMISSION_DENIED`, clear the
  stored org and re-select `me().organizations[0]` once before giving up.

### M3 (Medium) — Segment rename UX unverifiable until C1 fixed
- **Where:** `frontend/app/segments/page.tsx` rename flow
- **Evidence:** replay could not exercise the UI-visible rename path because C1 blocks profiles
  rendering. Backend rename confirmed working at service level (persisted into
  `profiles.segments[x].descriptive_label`). **Re-test required after C1.**

### L1 (Low) — Profile bar rows misalign with the rename button
- **Where:** `frontend/app/globals.css` `.bar-wrap` (3-column grid) vs 4 children in the segments
  profile rows (label, bar, count, rename button) → button wraps/misaligns.

### L2 (Low) — Export download uses a detached `<a>` element
- **Where:** `frontend/app/exports/page.tsx` (`link.click()` without `document.body.appendChild`)
- Works in Chromium; not guaranteed in Safari/Firefox. One-line robustness fix.

### L3 (Low) — Customer detail version selector shows raw UUID slices
- **Where:** `frontend/app/customers/[customerId]/page.tsx` — options read
  `version {uuid8}… (run {uuid8}…)`. Nicer: resolve version numbers (v1, v2) via the dataset
  detail endpoint. Cosmetic.

### L4 (Low) — Duplicate `/auth/me` calls on every navigation
- **Where:** `frontend/components/nav.tsx` + each page's `useAuthGuard` both run the guard.
  2+ identical `/auth/me` requests per page load. Harmless for a pilot; worth a shared
  request cache later.

### D-note (deployment, not a code bug)
- `NEXT_PUBLIC_API_URL` is baked at image build to `http://localhost:8000`. Works on the host
  machine; **a pilot user on another device will hit their own localhost** and get the
  "API unreachable" error page. Plan the real URL before PILOT-01 (compose arg already exists;
  no code change needed today).

### Cross-reference
- **UI-16 (Arabic/English):** already deferred with an honest note in `TASKS.md` — unchanged by
  this audit.

---

## Verified working (no issue)
- Register/login → TokenPair; `/auth/me` user + organizations selection (UI-02).
- Datasets list/create + row fields for the list table.
- Templates download without auth (plain `<a>` links work) (UI-03).
- Upload → `suggested_mapping` → PATCH → `ready_for_version` (UI-04). Synthetic CSVs map 1:1.
- Version create → poll → `valid` with `report.summary/issue_counts/issues` (UI-05), aside from H1.
- Churn training on a realistic dataset succeeded end-to-end; scoring 202 → job → band-colorable
  `low/medium/high` scores with `pages`/`total` pagination (UI-09).
- Customer detail unified score + segment contract (`data_version`, `top_signals[].feature`) (UI-10),
  aside from C2.
- Segmentation run lifecycle succeeded; compare endpoint (`segment_share_deltas`,
  `max_share_shift`, `caveats`) shape-correct for UI-15.
- Jobs list: fields `id/type/status/progress/created_at`, all 6 `job_type` filter values return
  only matching rows, `pages` present (UI-07).
- Jobs filters + export history list shape (UI-13), aside from H3 download.
- All 9 routes render crash-free headless, authed or not.

**Suggested fix order:** C1 → C2 → H1 → H2 → H3 → M1/M2 → re-test (incl. M3 rename e2e) → L-items.

---

## Resolution log (2026-11-05, same session) — ALL FIXED

All 9 issues + the D-note addressed; then `npx tsc --noEmit`, `next build`
(10 routes green), backend pytest (15 passed, incl. new assertions), full
docker rebuild + re-run of the e2e replay → **Total flagged: 0**, and a
headless Edge pass over all 9 routes → no render crashes.

| # | Fix applied |
|---|-------------|
| **C1** | `segments/page.tsx`: `RunDetail.profiles` retyped as `{ segments: Profile[]; labels_editable?: boolean } \| null`; UI unwraps `run.profiles?.segments`; rename gate also honored `run.profiles?.labels_editable`. Backend wrapper shape kept as the contract. |
| **C2** | `customers/[customerId]/page.tsx`: `signalDirectionClass()` maps `higher_risk`→▲/`dir-up`, `lower_risk`→▼/`dir-down` (`up`/`down` kept as fallbacks). |
| **H1** | `datasets/[id]/page.tsx`: `summaryValue()` renders nested dict values as `key1: v1 · key2: v2` (em-dash for empty objects) instead of `String(value)`. |
| **H2** | `app/page.tsx`: reads `metrics.selection.selected_model` (+ surfaced `rationale` next to the cutoff line). |
| **H3** | `docker-compose.yml`: `EXPORTS_DIR: /data/exports` on backend + worker, shared named volume `exports:/data/exports` mounted in both (mirrors the uploads pattern), new top-level volume declared. `.env.example` documents the requirement. Verified in-docker: export job succeeded and the backend streamed the worker-written CSV (no 404). |
| **M1** | `app/page.tsx`: bands now aggregated over the whole run via a bounded client loop (`useBandAggregate`, ≤10 pages × 200 = 2000 customers); single-page prev/next UI removed; a hint appears only when the 2000 cap was hit. |
| **M2** | `lib/api.ts` + `lib/hooks.ts`: `recoverActiveOrg()` re-selects `organizations[0]` on `403 PERMISSION_DENIED`; `useApiResource` retries the request exactly once after recovery, then surfaces the error (no loops). |
| **M3** | Re-tested in the replay after C1: rename via the UI-shaped call persisted to `profiles.segments[*].descriptive_label` → `[ok] UI-12 rename persisted`. |
| **L1** | `globals.css`: added `.bar-wrap-4` (`minmax(9rem,13rem) 1fr 9rem auto`) used by the segment profile rows; badge/count/action columns now align. |
| **L2** | `exports/page.tsx`: download `<a>` is appended to `document.body` before `click()` and removed after (Safari/Firefox-safe). |
| **L3** | Backend: `_run_schema(db, run)` resolves the run's `version_number` via an org-scoped `DatasetVersion` query in every churn model-run response (create/list/detail); asserted in `test_phase6.py`. Frontend version selector renders `version vN` when available. |
| **L4** | `lib/api.ts`: `/auth/me` TTL cache (30s) shared by nav + auth guard; invalidated in `saveTokens`/`clearTokens`/`saveActiveOrg`. |
| **D-note** | `.env.example` now documents that `NEXT_PUBLIC_API_URL` is baked at build time and must be set to the public server URL before `docker compose build` for remote pilot users. |

**Re-verification evidence (e2e replay, fresh user + 150-customer synthetic set):**
`[ok] customer detail shape`, `[ok] selected model = logistic_regression`,
`[ok] profile row shapes (wrapper {segments, labels_editable})`,
`[ok] rename persisted`, `[ok] compare shape`, `[ok] download ok (CSV streamed)`,
`[ok] jobs fields/filters`. `== ISSUE SUMMARY == No contract/UI-flow issues found.`
