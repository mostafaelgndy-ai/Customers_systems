# Security Review (TEST-17, TEST-18)

Review before onboarding real customer data. Environment configuration
(TEST-17) and security posture (TEST-18) as of 2026-11-06, backend
state after Phase 8. Each item states the finding and the release
requirement.

## 1. Environment / secrets (TEST-17)

| Area | State | Release requirement |
| --- | --- | --- |
| `SECRET_KEY` | Enforced at startup: refuses the dev default (`dev-secret-change-me`) or any value < 32 chars when `environment != dev` (config validator). | Use a generated 64-hex value per environment, stored outside git (host secret store / CI secret). |
| DB credentials | Root `.env` drives compose; backend `.env` separate for local runs. `change-me-strong-password` MUST be replaced. | Prod values via secret store; never commit. |
| CORS | `CORS_ORIGINS` explicit allow-list (comma string parsed via NoDecode + field validator; empty disables CORS). Currently `http://localhost:3000`. | Prod: only the real dashboard origin(s). |
| Token lifetimes | Access JWT 30 min, refresh JWT 7 d, `HS256`. | Acceptable for pilot. If HS256 is replaced, all tokens invalidate — plan sign-out. |
| Password hashing | bcrypt, 12 rounds default. | Keep ≥ 12 in prod. |
| Upload limits | Size/mime-bounded (HTTP 413/415 paths, TESTed). | Confirm disk quota on the uploads volume. |

## 2. Application security posture (TEST-18)

**Verified by tests (backend suite):**

- Tenant isolation on every query/mutation — cross-org resources return
  indistinguishable `404` (`test_tenant_isolation.py`).
- Role contract: reads require active membership; data mutations need
  `analyst`+; export **download** also `analyst`+ (explicit exception
  documented in `docs/api-conventions.md` §3) (`test_role_auth.py`).
- Unauthenticated access denied on all business endpoints
  (`test_auth.py`).
- Dataset deletion cascades derived artifacts; audit rows retained for
  traceability (SEC-06, `test_deletion.py`).
- Errors are opaque to clients — details only in structured logs keyed
  by `request_id` (SEC-05, errors.py).
- Rate limiting middleware (SEC-04): disabled by default for dev/tests;
  per-bucket limits exist for auth / uploads / expensive / default and
  respond `429 RATE_LIMITED`. **Release requirement:** enable
  `RATE_LIMIT_ENABLED=true` in prod.

**Gaps / recommendations, ranked:**

1. **HTTPS termination is not yet in the stack** — required before any
   real customer data (OPS-02, see DEPLOYMENT.md: Caddy reverse proxy
   with automatic TLS). JWTs and PII must not travel plaintext.
2. **SECRET_KEY rotation**: single-signing-key; rotation invalidates all
   sessions. For pilot, acceptable; document a maintenance window.
3. **No brute-force lockout** beyond auth rate limit 30/min — fine with
   rate limiting enabled; review in Phase 9 monitoring.
4. **Audit events** cover dataset/run lifecycle and export
   request/download — add auth (login/failed-login) events post-MVP.
5. **Upload content**: CSV-only enforcement + validation reports; no
   macro/zip preview — keep it that way.
6. **Physical models dir**: model artifacts are per-container local
   paths; when volumes are added (Phase 9), expose only to backend+worker
   service containers (no public read path).

## 3. Data subject / deletion readiness

- `DELETE /datasets/{id}` cascades files, versions, feature runs,
  scores, assignments (zero-children case regression-tested).
- Org/user-level deletion and full account export are NOT supported
  yet (pilot-limited, document to pilot customer in the pilot plan).
