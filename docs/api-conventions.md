# API Conventions

**Applies to:** all CustomerIQ backend endpoints (ENG-06, ENG-07)\ 
Base URL: `/api/v1`

## 1. Error envelope

Every non-2xx response uses one shape. `request_id` matches the
`X-Request-ID` response header and appears in server logs, so a client
report can be traced end-to-end (including background jobs it spawned).

```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Request body failed validation.",
    "field_errors": [
      { "field": "email", "message": "value is not a valid email address" }
    ],
    "request_id": "8f2c9c5e-…"
  }
}
```

Stable error `code` values (never change meaning after release):

| HTTP | code                  | Raised when |
|------|-----------------------|-------------|
| 400  | `BAD_REQUEST`         | Malformed request that is not a body-schema issue |
| 401  | `UNAUTHENTICATED`     | Missing/invalid/expired token |
| 403  | `PERMISSION_DENIED`   | Authenticated but role/organization check failed |
| 404  | `NOT_FOUND`           | Resource does not exist **or belongs to another organization** (indistinguishable by design) |
| 409  | `CONFLICT`            | Duplicate email, inconsistent state transitions |
| 413  | `PAYLOAD_TOO_LARGE`   | Upload exceeds size limit |
| 415  | `UNSUPPORTED_MEDIA_TYPE` | Uploaded file is not valid CSV content |
| 422  | `VALIDATION_ERROR`    | Request body/query schema violations (FastAPI) |
| 429  | RATE_LIMITED (future)  | Rate limit exceeded (SEC-04) |
| 500  | `INTERNAL_ERROR`      | Unhandled server error; details never leak to the client |

Domain-specific messages travel inside `message` / `field_errors`;
machine-readable detail stays in `code` (+ per-file validation issues
inside dataset version reports).

## 2. Pagination contract

List endpoints accept `page` (≥ 1, default 1) and `page_size`
(1–200, default 25). Response:

```json
{
  "items": [],
  "page": 1,
  "page_size": 25,
  "pages": 3,
  "total": 70
}
```

`pages = ceil(total / page_size)` (0 when the collection is empty).
Filtering and sorting combine with pagination; every list parameter is
bounded server-side.

## 3. Authentication & organization context

- `Authorization: Bearer <access-token>`; access tokens live
  `ACCESS_TOKEN_EXPIRES_MINUTES` (default 30 min); refresh tokens are
  longer-lived JWTs and rotate on `/auth/refresh`.
- All resources are organization-scoped. The acting organization is
  resolved from membership: if the user belongs to exactly one
  organization it is used; otherwise the caller must send
  `X-Organization-ID: <org-uuid>`.
- Roles: `owner > admin > analyst > member`.
  - read endpoints: any active membership
  - data mutations (upload, mapping, run creation): `analyst`, `admin`, `owner`
  - organization administration: `owner` / `admin`
- Tenant isolation is enforced **server-side on every query and
  mutation**; another organization's resource returns `404`, not `403`,
  to avoid resource-name disclosures.
- **Explicit exception:** export download (`GET /exports/{job_id}/download`)
  requires `analyst`+ (like data mutations, not read-only membership).
  PII-bearing scored exports are the primary data-exfiltration surface,
  so deliverables are intentionally gated higher than in-app reads.

## 4. Correlation & logging (ENG-07)

- Each request gets an `X-Request-ID` (accepted from the client if
  provided, otherwise generated). It is attached to every log line and
  propagated into background jobs (`Job.correlation_id`).
- Logs are structured JSON. **Raw customer file contents, IDs beyond
  the canonical `customer_id`/`order_id` context, emails and secrets
  are never logged.**

## 5. Background jobs

HTTP requests never block on long-running processing. Jobs are stored
in the `jobs` table and expose:

```json
{ "id": "…", "type": "validate_dataset", "status": "running",
  "progress": 40, "error": null, "correlation_id": "…",
  "created_at": "…", "started_at": "…", "finished_at": null }
```

`status ∈ queued | running | succeeded | failed | cancelled`.
In `dev` with `INLINE_JOBS=true`, jobs execute synchronously in-process
(acceptable: uploads/validation are small; OBEY the same accounting).
In `staging`/`prod` they must run on Celery workers (PRD §7.8).

## 6. Health & readiness

- `GET /api/v1/health` — liveness (process up, no dependencies touched)
- `GET /api/v1/health/readiness` — checks DB & Redis connectivity
