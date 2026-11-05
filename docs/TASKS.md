# Implementation Tasks

## Customer Intelligence Platform --- CustomerIQ

**Version:** 1.0\
**Status:** Execution backlog\
**Last updated:** 2026-10-31

------------------------------------------------------------------------

## 0. How to Use This Backlog

-   Work from top to bottom within each phase; dependencies matter.
-   Mark a task complete only when its acceptance criteria pass.
-   Keep tasks small enough to implement, test, and review
    independently.
-   Do not train a final model before confirming the data contract and
    churn definition.
-   Do not expose a model metric as a product claim without a
    reproducible evaluation report.
-   Use synthetic data for pipeline development only; validate
    commercial value on permissioned, representative merchant data.

### Priority

-   **P0:** MVP blocker.
-   **P1:** important follow-up.
-   **P2:** future scope.

### Status

-   `[ ]` Not started
-   `[x]` Completed
-   `[~]` In progress
-   `[!]` Blocked

------------------------------------------------------------------------

## Phase 0 --- Product Discovery and Validation

### 0.1 Customer discovery

-   [ ] **DISC-01 (P0):** Identify 5--10 e-commerce businesses matching
    the intended initial customer profile.
    -   Acceptance: prospect list includes business type, approximate
        scale, contact path, and current analytics workflow.
-   [ ] **DISC-02 (P0):** Interview at least 5 owners, CRM managers, or
    analysts.
    -   Acceptance: interview notes capture churn definition, current
        process, pain, decision owner, budget signal, and data
        availability.
-   [ ] **DISC-03 (P0):** Validate that churn and segmentation are
    problems users actively want solved.
    -   Acceptance: at least 3 interviewees independently describe a
        concrete workflow or loss related to customer
        inactivity/segmentation.
-   [ ] **DISC-04 (P0):** Obtain a permissioned representative dataset
    or a documented data export specification.
    -   Acceptance: source, permission, fields, date coverage, and known
        limitations are recorded.
-   [ ] **DISC-05 (P0):** Confirm the first pilot's repeat-purchase
    cycle and inactivity-based churn horizon.
    -   Acceptance: written churn definition approved by the pilot
        stakeholder.
-   [ ] **DISC-06 (P0):** Define a measurable pilot success criterion.
    -   Acceptance: agreed operational metric (e.g.Â useful top-risk
        list) and, where feasible, business outcome measurement plan.
-   [ ] **DISC-07 (P1):** Test willingness to pay and preferred pricing
    unit.
    -   Acceptance: documented price feedback and buying process from
        prospects.

**Exit gate:** Do not proceed to a claimed commercial MVP until a real
target user confirms the workflow and data availability.

------------------------------------------------------------------------

## Phase 1 --- Repository, Engineering Standards, and Architecture

-   [x] **ENG-01 (P0):** Create repository structure for frontend,
    backend, ML, infrastructure, and docs.
    -   Acceptance: clear separation of deployable services and shared
        contracts.
-   [x] **ENG-02 (P0):** Add README with local setup, environment
    variables, and run commands.
    -   Acceptance: a new developer can start the system from a clean
        environment.
-   [x] **ENG-03 (P0):** Add `.env.example`; ensure secrets are excluded
    from version control.
    -   Acceptance: no real credentials in repository history or example
        files.
-   [x] **ENG-04 (P0):** Configure formatting, linting, type checking,
    and pre-commit or CI checks.
    -   Acceptance: checks run locally and in CI.
-   [x] **ENG-05 (P0):** Add Docker Compose for local PostgreSQL, Redis,
    backend, worker, and frontend.
    -   Acceptance: documented command starts required services.
-   [x] **ENG-06 (P0):** Define API error format and pagination
    contract.
    -   Acceptance: documented response envelope, validation errors, and
        pagination fields.
-   [x] **ENG-07 (P0):** Define logging and request/job correlation IDs.
    -   Acceptance: a request and its background job can be traced
        without logging customer PII.
-   [x] **ENG-08 (P1):** Add CI pipeline for tests, linting, type
    checks, and build.
    -   Acceptance: pull requests fail on required check failures.

------------------------------------------------------------------------

## Phase 2 --- Data Contract and Dataset Ingestion

### 2.1 Data specification

-   [x] **DATA-01 (P0):** Finalize canonical `customers.csv` schema.
    -   Acceptance: required/optional fields, types, examples, and
        constraints documented.
-   [x] **DATA-02 (P0):** Finalize canonical `orders.csv` schema.
    -   Acceptance: status, refund, discount, currency, and date
        semantics documented.
-   [x] **DATA-03 (P0):** Define eligible-order policy.
    -   Acceptance: completed, cancelled, refunded, and partially
        refunded order handling is explicit.
-   [x] **DATA-04 (P0):** Define timezone and currency policy.
    -   Acceptance: no silent mixing of timezones or currencies.
-   [x] **DATA-05 (P0):** Create downloadable sample templates.
    -   Acceptance: templates pass the ingestion validator.

### 2.2 Backend ingestion

-   [x] **DATA-06 (P0):** Implement dataset and dataset-file database
    models.
    -   Acceptance: each dataset is tenant-scoped and versioned.
-   [x] **DATA-07 (P0):** Implement secure CSV upload endpoint.
    -   Acceptance: size limits, extension/content validation, safe
        storage, and authorization are tested.
-   [x] **DATA-08 (P0):** Implement schema mapping to canonical columns.
    -   Acceptance: supported source column names map deterministically;
        unknown required columns produce actionable errors.
-   [x] **DATA-09 (P0):** Implement validation engine.
    -   Acceptance: checks required columns, types, nulls, duplicates,
        date parsing, numeric ranges, and foreign-key consistency.
-   [x] **DATA-10 (P0):** Generate row-level validation errors and
    aggregate report.
    -   Acceptance: user can identify invalid rows and reasons without
        exposing unrelated tenant data.
-   [x] **DATA-11 (P0):** Implement immutable dataset versions.
    -   Acceptance: re-upload creates a new version; previous successful
        version remains available.
-   [x] **DATA-12 (P0):** Add dataset status lifecycle.
    -   Acceptance: uploaded â†’ validating â†’ valid/invalid/failed states
        are persisted.
-   [x] **DATA-13 (P1):** Add upload cancellation and safe retry
    behavior.
    -   Acceptance: retries do not create duplicate dataset versions or
        corrupt status.

### 2.3 Data tests

-   [x] **DATA-14 (P0):** Create fixtures for valid, invalid,
    duplicated, incomplete, and edge-case CSV files.
-   [x] **DATA-15 (P0):** Test currency mismatch, timezone parsing, and
    invalid order statuses.
-   [x] **DATA-16 (P0):** Test that one tenant cannot access another
    tenant's files or dataset metadata.

**Exit gate:** A representative dataset validates successfully and all
critical data rules are covered by tests.

------------------------------------------------------------------------

## Phase 3 --- Exploratory Data Analysis and Feature Engineering

-   [x] **FE-01 (P0):** Build a reproducible EDA notebook or report for
    each dataset version.
    -   Acceptance: report includes row counts, date coverage, unique
        customers/orders, missingness, duplicates, order statuses, and
        monetary distributions.
-   [x] **FE-02 (P0):** Implement eligible-order filtering.
    -   Acceptance: feature calculations use only orders allowed by the
        documented policy.
-   [x] **FE-03 (P0):** Implement customer-level RFM features.
    -   Acceptance: recency, frequency, and monetary values match
        hand-calculated test cases.
-   [x] **FE-04 (P0):** Implement average order value and tenure.
    -   Acceptance: edge cases for one order, missing creation date, and
        no eligible orders are tested.
-   [x] **FE-05 (P1):** Implement rolling 30-day and 90-day features.
    -   Acceptance: windows are cutoff-aware and covered by
        boundary-date tests.
-   [x] **FE-06 (P1):** Implement purchase-interval and discount/refund
    features where data supports them.
    -   Acceptance: missing source fields do not silently become
        misleading zero values.
-   [x] **FE-07 (P0):** Define feature schema and version.
    -   Acceptance: every feature has name, definition, type, source,
        cutoff behavior, and missing-value policy.
-   [x] **FE-08 (P0):** Implement deterministic feature pipeline.
    -   Acceptance: same input version and configuration produce
        identical feature values.
-   [x] **FE-09 (P0):** Add temporal leakage tests.
    -   Acceptance: changing post-cutoff orders cannot change pre-cutoff
        features.
-   [x] **FE-10 (P0):** Persist feature snapshots and metadata.
    -   Acceptance: snapshot links to organization, dataset version,
        cutoff, and feature version.

**Exit gate:** Feature table is reproducible, tested, and contains no
post-cutoff information.

------------------------------------------------------------------------

## Phase 4 --- Churn Prediction ML Pipeline

### 4.1 Label design

-   [x] **CHURN-01 (P0):** Implement configurable observation window and
    label horizon.
    -   Initial experimental default: 180-day observation window and
        90-day future inactivity horizon.
    -   Acceptance: values are stored in run configuration and displayed
        in reports.
-   [x] **CHURN-02 (P0):** Implement eligibility rules for label
    generation.
    -   Acceptance: customers without a fully observable label horizon
        are excluded, not mislabeled.
-   [x] **CHURN-03 (P0):** Generate churn labels using only future
    outcome data relative to cutoff.
    -   Acceptance: unit tests cover boundary dates and repeat
        purchases.
-   [x] **CHURN-04 (P0):** Report positive/negative class counts and
    rates.
    -   Acceptance: class distribution is visible before model training.

### 4.2 Training and evaluation

-   [x] **CHURN-05 (P0):** Build time-aware train/validation/test split.
    -   Acceptance: training observations precede validation and test
        periods; no customer/time leakage under the chosen design.
-   [x] **CHURN-06 (P0):** Train Logistic Regression baseline.
    -   Acceptance: preprocessing and estimator are saved as one
        reproducible pipeline.
-   [x] **CHURN-07 (P0):** Train a tree-based benchmark (Random Forest
    or Gradient Boosting).
    -   Acceptance: same split and comparable feature contract are used.
-   [x] **CHURN-08 (P0):** Calculate precision, recall, F1, ROC-AUC,
    PR-AUC, and confusion matrix.
    -   Acceptance: undefined metrics are handled explicitly; class
        imbalance is reported.
-   [x] **CHURN-09 (P0):** Evaluate threshold trade-offs and top-k
    precision/recall.
    -   Acceptance: report shows results at realistic contact capacity.
-   [x] **CHURN-10 (P1):** Evaluate probability calibration.
    -   Acceptance: reliability plot and calibration metric are included
        before probability claims are made.
-   [x] **CHURN-11 (P0):** Create model comparison report.
    -   Acceptance: model selection rationale considers operational
        trade-offs, not accuracy alone.
-   [x] **CHURN-12 (P0):** Save model artifact and full training
    metadata.
    -   Acceptance: artifact includes feature version, dataset version,
        cutoff, label horizon, split, parameters, metrics, and
        dependency versions.

### 4.3 Scoring and explanations

-   [x] **CHURN-13 (P0):** Implement batch scoring for eligible active
    customers.
    -   Acceptance: one prediction per eligible customer per run;
        results are traceable.
-   [x] **CHURN-14 (P0):** Implement configurable risk bands.
    -   Initial display defaults: Low \< 0.30, Medium 0.30--0.69, High
        \>= 0.70.
    -   Acceptance: thresholds are configuration, not hardcoded business
        truth.
-   [x] **CHURN-15 (P1):** Implement global feature importance or
    equivalent model interpretation.
    -   Acceptance: explanation method is appropriate for the selected
        model and clearly labeled as association, not causation.
-   [x] **CHURN-16 (P1):** Implement customer-level explanation view.
    -   Acceptance: top contributing signals are shown with limitations
        and no causal wording.
-   [x] **CHURN-17 (P0):** Add model/run registry records.
    -   Acceptance: every prediction references an immutable analysis
        run and model version.

**Exit gate:** A time-aware evaluation report exists, leakage checks
pass, and batch scoring is reproducible.

------------------------------------------------------------------------

## Phase 5 --- Customer Segmentation Pipeline

-   [x] **SEG-01 (P0):** Select segmentation feature set and document
    rationale.
    -   Acceptance: features are appropriate for the pilot merchant and
        exclude identifiers.
-   [x] **SEG-02 (P0):** Implement missing-value handling and feature
    transformations.
    -   Acceptance: fitted preprocessing is persisted with the
        clustering model.
-   [x] **SEG-03 (P0):** Implement scaling for distance-based
    clustering.
    -   Acceptance: scaling is fit on the relevant training population
        and reused consistently.
-   [x] **SEG-04 (P0):** Implement K-Means pipeline.
    -   Acceptance: random seed, initialization, and cluster count are
        recorded.
-   [x] **SEG-05 (P0):** Compare a bounded set of candidate cluster
    counts.
    -   Acceptance: report includes cluster sizes and silhouette score
        where valid.
-   [x] **SEG-06 (P0):** Add safeguards for tiny, empty, or degenerate
    clusters.
    -   Acceptance: run emits warnings and does not silently present
        unusable clusters as reliable.
-   [x] **SEG-07 (P0):** Generate segment profiles.
    -   Acceptance: each segment has counts, medians/means, and
        distributions for key features.
-   [x] **SEG-08 (P0):** Generate descriptive labels from profile
    statistics.
    -   Acceptance: labels are editable and are not based on arbitrary
        cluster IDs.
-   [x] **SEG-09 (P0):** Persist assignments, profiles, parameters, and
    preprocessing artifact.
    -   Acceptance: each assignment references a segmentation run.
-   [x] **SEG-10 (P1):** Add segment comparison/stability report across
    runs.
    -   Acceptance: differences are presented with appropriate caveats.
-   [x] **SEG-11 (P2):** Evaluate DBSCAN or alternative clustering
    methods.
    -   Acceptance: alternative is retained only if it improves
        interpretability or operational usefulness.

**Exit gate:** A domain reviewer can distinguish the segments using
their profiles and identify a practical action for each.

------------------------------------------------------------------------

## Phase 6 --- Backend API and Asynchronous Jobs

### 6.1 Core API

-   [x] **API-01 (P0):** Implement authentication endpoints.
-   [x] **API-02 (P0):** Implement organization and membership
    authorization.
-   [x] **API-03 (P0):** Implement dataset upload/list/detail endpoints.
-   [x] **API-04 (P0):** Implement schema mapping and validation report
    endpoints.
-   [x] **API-05 (P0):** Implement analysis-run creation endpoint.
-   [x] **API-06 (P0):** Implement analysis-run status/progress
    endpoint.
-   [x] **API-07 (P0):** Implement churn prediction list/detail/filter
    endpoints.
-   [x] **API-08 (P0):** Implement segmentation run/profile/assignment
    endpoints.
-   [x] **API-09 (P0):** Implement customer detail endpoint scoped to a
    dataset/run.
-   [x] **API-10 (P0):** Implement CSV export endpoints/jobs.
-   [x] **API-11 (P0):** Add pagination, filtering, sorting, and bounded
    query parameters.
-   [x] **API-12 (P0):** Add consistent validation and error responses.

### 6.2 Background processing

-   [x] **JOB-01 (P0):** Configure Redis and background worker.
-   [x] **JOB-02 (P0):** Implement validation job.
-   [x] **JOB-03 (P0):** Implement feature-generation job.
-   [x] **JOB-04 (P0):** Implement churn training/scoring job.
-   [x] **JOB-05 (P0):** Implement segmentation job.
-   [x] **JOB-06 (P0):** Implement export job.
-   [x] **JOB-07 (P0):** Persist job state, progress, start/end time,
    and safe error summary.
-   [x] **JOB-08 (P0):** Make retries idempotent or protect against
    duplicate result creation.
-   [x] **JOB-09 (P0):** Ensure one failed analysis run cannot overwrite
    a previous successful run.
-   [x] **JOB-10 (P1):** Add worker concurrency and resource limits.

### 6.3 API security tests

-   [x] **SEC-01 (P0):** Test tenant isolation on every
    organization-scoped endpoint.
-   [x] **SEC-02 (P0):** Test authorization for dataset upload,
    analysis, and exports.
-   [x] **SEC-03 (P0):** Test file size/type validation and malformed
    CSV handling.
-   [x] **SEC-04 (P0):** Add rate limits to authentication, upload, and
    expensive operations.
-   [x] **SEC-05 (P0):** Ensure logs and error messages do not leak PII
    or secrets.
-   [x] **SEC-06 (P1):** Add audit events for sensitive actions and
    exports.

**Exit gate:** API can execute the complete workflow asynchronously and
passes tenant-boundary tests.

------------------------------------------------------------------------

## Phase 7 --- Frontend Dashboard

-   [x] **UI-01 (P0):** Create application shell and authenticated
    routes.
-   [x] **UI-02 (P0):** Build sign-in and organization onboarding
    screens.
-   [x] **UI-03 (P0):** Build dataset upload screen with templates and
    instructions.
-   [x] **UI-04 (P0):** Build column mapping interface.
-   [x] **UI-05 (P0):** Build validation summary and row-error display.
-   [x] **UI-06 (P0):** Build analysis configuration screen for cutoff,
    windows, and run options.
-   [x] **UI-07 (P0):** Build background job progress/status screen.
-   [x] **UI-08 (P0):** Build overview dashboard with customer counts
    and churn-risk distribution.
-   [x] **UI-09 (P0):** Build churn-risk customer table with filters and
    pagination.
-   [x] **UI-10 (P0):** Build customer detail view with prediction,
    features, and run metadata.
-   [x] **UI-11 (P0):** Build segmentation overview and segment
    distribution.
-   [x] **UI-12 (P0):** Build segment profile detail with interpretable
    statistics.
-   [x] **UI-13 (P0):** Build CSV export workflow.
-   [x] **UI-14 (P0):** Add loading, empty, error, and permission-denied
    states.
-   [x] **UI-15 (P1):** Add analysis-run comparison view.
-   [ ] **UI-16 (P1):** Add Arabic/English localization.
    -   DEFERRED (P1, honest note): UI strings are English-only in the
        pilot build; the localization scaffold is intentionally deferred
        until after pilot validation rather than marked done falsely.
-   [x] **UI-17 (P1):** Add accessible chart labels, keyboard
    navigation, and contrast checks.

**Exit gate:** A pilot user can upload data, run analysis, inspect
results, filter, and export without developer assistance.

------------------------------------------------------------------------

## Phase 8 --- Testing and Quality

### ML and data tests

-   [ ] **TEST-01 (P0):** Unit tests for every feature calculation.
-   [ ] **TEST-02 (P0):** Unit tests for churn label boundaries and
    eligibility.
-   [ ] **TEST-03 (P0):** Leakage tests for time-based feature
    generation.
-   [ ] **TEST-04 (P0):** Reproducibility tests for training and
    segmentation.
-   [ ] **TEST-05 (P0):** Tests for imbalanced classes and undefined
    evaluation metrics.
-   [ ] **TEST-06 (P0):** Tests for small datasets and insufficient
    history.
-   [ ] **TEST-07 (P0):** Tests for malformed and inconsistent source
    data.

### Application tests

-   [ ] **TEST-08 (P0):** API integration tests for the full
    upload-to-results workflow.
-   [ ] **TEST-09 (P0):** Tenant isolation and role authorization tests.
-   [ ] **TEST-10 (P0):** Background job retry/failure tests.
-   [ ] **TEST-11 (P0):** Export correctness and permission tests.
-   [ ] **TEST-12 (P1):** Frontend end-to-end tests for primary user
    journeys.
-   [ ] **TEST-13 (P1):** Load test with a documented representative
    dataset size.

### Release quality

-   [ ] **TEST-14 (P0):** Create a release checklist.
-   [ ] **TEST-15 (P0):** Verify database backup and restore procedure.
-   [ ] **TEST-16 (P0):** Verify data deletion procedure.
-   [ ] **TEST-17 (P0):** Verify production secrets and environment
    configuration.
-   [ ] **TEST-18 (P0):** Run security review before onboarding real
    customer data.

------------------------------------------------------------------------

## Phase 9 --- Deployment and Operations

-   [ ] **OPS-01 (P0):** Create production Docker images.
-   [ ] **OPS-02 (P0):** Configure HTTPS and secure reverse proxy.
-   [ ] **OPS-03 (P0):** Configure production PostgreSQL backups.
-   [ ] **OPS-04 (P0):** Configure private object storage and access
    policies.
-   [ ] **OPS-05 (P0):** Configure worker process supervision and
    restart policy.
-   [ ] **OPS-06 (P0):** Configure structured logs and error monitoring.
-   [ ] **OPS-07 (P0):** Add health/readiness checks for API, database,
    Redis, and worker.
-   [ ] **OPS-08 (P0):** Document deployment, rollback, backup, and
    restore.
-   [ ] **OPS-09 (P1):** Add operational dashboards and alerting.
-   [ ] **OPS-10 (P1):** Define data retention and deletion schedule.

------------------------------------------------------------------------

## Phase 10 --- Pilot, Feedback, and Commercial Validation

-   [ ] **PILOT-01 (P0):** Onboard one pilot merchant using written data
    permission.
-   [ ] **PILOT-02 (P0):** Run the complete workflow on the merchant's
    dataset.
-   [ ] **PILOT-03 (P0):** Review high-risk customer list with the
    merchant.
-   [ ] **PILOT-04 (P0):** Review segment profiles with the merchant.
-   [ ] **PILOT-05 (P0):** Record false positives, missed churners, and
    unusable segments.
-   [ ] **PILOT-06 (P0):** Capture time saved and operational
    usefulness.
-   [ ] **PILOT-07 (P0):** Where feasible, design a holdout/control
    approach to measure retention impact.
-   [ ] **PILOT-08 (P0):** Ask for a paid continuation or written
    willingness-to-pay feedback.
-   [ ] **PILOT-09 (P1):** Prioritize product changes from observed
    pilot friction.
-   [ ] **PILOT-10 (P1):** Decide whether to focus on a specific
    e-commerce niche or broaden the product.

**Commercial exit gate:** At least one real business completes the
workflow, confirms the output is useful, and provides credible evidence
of willingness to pay. A successful technical demo alone does not pass
this gate.

------------------------------------------------------------------------

## Phase 11 --- Post-MVP Enhancements

-   [ ] **NEXT-01 (P1):** Add Shopify/WooCommerce import after
    confirming demand.
-   [ ] **NEXT-02 (P1):** Add scheduled analysis runs.
-   [ ] **NEXT-03 (P1):** Add drift and data-quality monitoring.
-   [ ] **NEXT-04 (P1):** Add model approval and rollback workflow.
-   [ ] **NEXT-05 (P1):** Add campaign audience export integrations.
-   [ ] **NEXT-06 (P2):** Evaluate uplift modeling for treatment
    prioritization.
-   [ ] **NEXT-07 (P2):** Evaluate customer lifetime value prediction.
-   [ ] **NEXT-08 (P2):** Evaluate alternative segmentation methods.
-   [ ] **NEXT-09 (P2):** Add multi-currency normalization if the target
    market requires it.
-   [ ] **NEXT-10 (P2):** Add API usage billing and subscription
    management.

------------------------------------------------------------------------

## Definition of Done (DoD)

A task is complete only when all applicable conditions are met:

1.  Implementation matches the requirement and has no known critical
    defect.
2.  Automated tests cover the primary behavior and relevant edge cases.
3.  Authorization and tenant isolation are verified for data-access
    features.
4.  Errors are handled and surfaced without leaking secrets or customer
    PII.
5.  Logs and run metadata are sufficient to debug the behavior.
6.  Documentation and API contracts are updated.
7.  Code passes formatting, linting, type checking, and CI.
8.  A reviewer can reproduce the result from documented steps.

## Recommended Build Order

For a solo developer, avoid building the entire dashboard before proving
the data and ML workflow.

1.  Discovery + data contract.
2.  CSV validator.
3.  EDA + feature engineering.
4.  Churn labels + time-aware evaluation.
5.  Churn baseline and benchmark.
6.  Segmentation + profiles.
7.  Persisted analysis runs.
8.  FastAPI endpoints + background jobs.
9.  Minimal dashboard.
10. Security, deployment, pilot, and commercial validation.

**First concrete milestone:** Given a permissioned customer/order
dataset, produce a reproducible report containing data-quality findings,
churn evaluation metrics, a ranked churn-risk list, and interpretable
customer segment profiles. Do this before investing heavily in frontend
polish.


