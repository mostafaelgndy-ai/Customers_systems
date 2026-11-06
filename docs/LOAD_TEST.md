# Load Test (TEST-13)

One representative pilot workflow, executed end to end against a live
stack with per-phase wall-clock timings. Harness:
`loadtest/load_test.mjs` (Node >= 18, synthetic data generator).

## What it measures

```
register/org → upload customers+orders CSVs → accept suggested mapping
→ validate version → train churn model (full pipeline) → batch scoring
→ browse (scores list paged + customer detail) → segmentation (k sweep)
→ create export → download CSV → delete dataset (cascade)
```

## Environment of the recorded run

- Docker stack on one machine: backend + Postgres 16 + Redis + worker
  (`concurrency=2`), frontend not involved (API-level harness).
- Dataset: **3,000 customers / ~15,000 orders** (18 months of history).
- Application defaults (180-day observation window, 90-day horizon,
  k candidates [4,5,6,7,8]).

## Results (representative run)

| Phase | Duration |
| --- | --- |
| Upload + suggested mapping (2 CSVs) | ~2,400 ms |
| Validate version | 1,289 ms |
| Train churn model (3 label periods) | 72,380 ms |
| Batch scoring (2,541 eligible customers) | 16,530 ms |
| Browse: `GET /churn/scores` page 1 | avg ~150 ms (spread 86–195 ms) |
| Browse: `GET /customers/{id}` detail | 12–14 ms |
| Segmentation (k sweep [4..8]) | 13,973 ms |
| Export: create job | 1,284 ms |
| Export: download (0.27 MB, 2,541 rows) | 16 ms |
| Delete dataset (full cascade) | 204 response |
| **Total pipeline wall clock** | **≈ 106 s** |

A small sanity run (50 customers) completes the whole flow in ~8.1 s.

The harness asserts status codes and response shapes on every step,
prints a `== phase timings ==` table, and exits `0` only if the whole
journey succeeds (including the cascade deletion at the end, which also
cleans the org up between runs).

## How to re-run

```bash
docker compose up -d          # healthy stack
BASE=http://localhost:8000 node loadtest/load_test.mjs
# optional: CUSTOMERS=3000 ORDERS_PER=5
```

## Interpretation / scale reading

- The dominant cost is training (≈ 68% of total), which runs as a
  background job — users are not blocked while it runs.
- Interactive read paths stay well under 200 ms at 3k customers.
- This is a single-host dev-machines run; treat it as a floor test of
  the pilot scale (~3k customers), not a capacity certificate.

## Known gaps

- Browser-level rendering latency is not measured (harness is API-level;
  UI response shapes are pinned separately by `frontend/e2e/run_flow_test.mjs`).
- No concurrent multi-user simulation here; load testing of concurrent
  tenants is deferred until pilot traffic patterns are known.
