# Product Requirements Document (PRD)

## Customer Intelligence Platform

**Product codename:** CustomerIQ\
**Version:** 1.0\
**Status:** Draft --- implementation baseline\
**Last updated:** 2026-10-31

------------------------------------------------------------------------

## 1. Executive Summary

CustomerIQ is a B2B analytics platform for e-commerce businesses. It
uses historical customer and order data to:

1.  Estimate which active customers are likely to stop purchasing
    (**Customer Churn Prediction**).
2.  Discover behavior-based customer groups (**Customer Segmentation**).
3.  Translate model outputs into understandable, actionable business
    recommendations.

The product is not intended to be a generic notebook or a model-demo
page. It is a repeatable workflow: upload customer/order data, validate
it, generate features, run analytical models, inspect results, export
target lists and reports, and monitor model quality over time.

**Initial commercial hypothesis:** small and medium e-commerce
businesses need a practical way to identify customers at risk of
becoming inactive and understand customer value/behavior without hiring
a dedicated data science team.

This hypothesis must be validated with prospective users before treating
the product as commercially proven.

## 2. Product Vision

Make customer behavior understandable and actionable for businesses that
have transactional data but limited in-house machine-learning capacity.

### 2.1 Product principles

-   Business outcomes take priority over impressive model metrics.
-   Every prediction must be traceable to a model version and data
    snapshot.
-   Users must be able to understand why a customer was flagged.
-   Data quality problems must be reported, not silently hidden.
-   The platform must not claim that correlation or model attribution
    proves causation.
-   Customer data must be isolated between organizations.
-   Predictions are decision support; users remain responsible for
    marketing actions.

## 3. Problem Statement

E-commerce businesses often store customer and order data in
spreadsheets, store platforms, or databases. They may struggle to
answer:

-   Which previously active customers are likely to stop buying?
-   Which customers deserve retention attention first?
-   Which customers generate the most revenue or repeat purchases?
-   What meaningful behavioral groups exist in the customer base?
-   How should marketing teams tailor campaigns to different groups?
-   Did a retention campaign improve outcomes?

Manual spreadsheet analysis is slow, inconsistent, and difficult to
repeat. A model-only solution is insufficient if its results cannot be
interpreted or operationalized.

## 4. Goals and Non-Goals

### 4.1 Goals (MVP)

1.  Accept structured customer and order data through CSV upload.
2.  Validate schema, types, duplicates, missing values, and date ranges.
3.  Create customer-level behavioral features using a defined
    observation window.
4.  Train and evaluate a churn classification model using time-aware
    validation.
5.  Score eligible customers and present risk probabilities and risk
    bands.
6.  Segment customers using behavioral and monetary features.
7.  Profile segments with interpretable statistics and descriptive
    labels.
8.  Provide a dashboard with actionable filters and customer lists.
9.  Export scored customers, segment membership, and summary reports.
10. Track model versions, dataset versions, and analysis runs.
11. Enforce tenant-level data isolation and role-based access.
12. Provide clear limitations and data-quality warnings.

### 4.2 Non-goals (MVP)

-   Real-time event streaming or live scoring.
-   Automatic marketing campaign execution.
-   Direct integrations with Shopify, WooCommerce, Magento, or CRM
    systems.
-   Automated causal measurement or uplift modeling.
-   Fully automated campaign content generation.
-   Medical, credit, employment, or other high-impact decisions.
-   A universal model that works equally well for every industry.
-   Guaranteeing that a customer will churn.
-   Automatic retraining without validation and approval.

These may be considered after validating demand and the reliability of
the MVP.

## 5. Target Users and Personas

### Persona A --- E-commerce Owner / General Manager

-   Wants to understand revenue and customer retention.
-   Has limited time and limited ML knowledge.
-   Needs concise dashboards and exportable reports.

### Persona B --- Marketing / CRM Manager

-   Needs lists of high-risk customers.
-   Wants to filter customers by risk, value, and segment.
-   Needs practical prioritization, not only charts.

### Persona C --- Data Analyst

-   Uploads data, investigates quality issues, compares runs.
-   Needs transparent metrics, feature definitions, and exports.

### Persona D --- Organization Admin

-   Manages users, access, datasets, and organization settings.
-   Needs tenant security and auditability.

## 6. Core Use Cases

### UC-01: Upload a dataset

An authorized user uploads customer and order CSV files, maps columns if
necessary, and receives a validation report before analysis.

### UC-02: Run churn analysis

A user selects an approved model version and analysis configuration. The
platform creates customer features, scores eligible customers, and
displays churn-risk probabilities.

### UC-03: Investigate a high-risk customer

A user opens a customer record and reviews relevant behavioral
indicators and model explanation signals. The UI must not present these
signals as causal explanations.

### UC-04: Segment customers

A user runs segmentation with a supported configuration, reviews cluster
profiles, and assigns business-friendly labels.

### UC-05: Export an audience

A user filters by churn risk, segment, purchase recency, or customer
value and exports the resulting customer list.

### UC-06: Compare analysis runs

An analyst compares run dates, dataset versions, model versions,
evaluation metrics, and customer counts.

## 7. Product Scope and Functional Requirements

Priority definitions: - **P0:** required for MVP launch. - **P1:**
valuable, but may follow the first usable release. - **P2:** future
enhancement.

### 7.1 Authentication and Organization Management

  -----------------------------------------------------------------------
  ID                      Requirement             Priority
  ----------------------- ----------------------- -----------------------
  AUTH-01                 Users can register and  P0
                          sign in securely.       

  AUTH-02                 Every user belongs to   P0
                          an organization/tenant. 

  AUTH-03                 Organization data is    P0
                          isolated at the API and 
                          database layers.        

  AUTH-04                 Roles include           P0
                          Owner/Admin and         
                          Analyst/Member.         

  AUTH-05                 Admin can invite,       P1
                          deactivate, and manage  
                          organization members.   

  AUTH-06                 Sensitive actions are   P1
                          recorded in an audit    
                          log.                    
  -----------------------------------------------------------------------

### 7.2 Data Ingestion and Validation

  -----------------------------------------------------------------------
  ID                      Requirement             Priority
  ----------------------- ----------------------- -----------------------
  DATA-01                 Upload customer CSV and P0
                          order CSV independently 
                          or as a documented      
                          combined format.        

  DATA-02                 Provide a downloadable  P0
                          CSV template and schema 
                          documentation.          

  DATA-03                 Support column mapping  P0
                          for known canonical     
                          fields.                 

  DATA-04                 Validate required       P0
                          columns, data types,    
                          dates, numeric ranges,  
                          and identifiers.        

  DATA-05                 Detect duplicate        P0
                          customer IDs and        
                          duplicate order IDs.    

  DATA-06                 Report missing values   P0
                          and invalid rows with   
                          row-level reasons.      

  DATA-07                 Show validation summary P0
                          before the user starts  
                          an analysis.            

  DATA-08                 Preserve original       P0
                          uploaded files and      
                          associate them with an  
                          immutable dataset       
                          version.                

  DATA-09                 Allow users to cancel   P1
                          or retry failed         
                          processing jobs safely. 

  DATA-10                 Integrate directly with P2
                          commerce platforms.     
  -----------------------------------------------------------------------

#### Canonical input schema

The exact input can be mapped by the user, but the internal
representation must use stable canonical names.

**customers.csv**

  Field                 Type            Required Description
  --------------------- ---------- ------------- -----------------------------------
  customer_id           string               Yes Stable unique customer identifier
  created_at            datetime     Recommended Customer creation timestamp
  country               string                No Country/market
  acquisition_channel   string                No Acquisition source
  customer_status       string                No Source-system status

**orders.csv**

  Field             Type            Required Description
  ----------------- ---------- ------------- -------------------------------------
  order_id          string               Yes Unique order identifier
  customer_id       string               Yes Customer foreign key
  order_date        datetime             Yes Order timestamp
  order_status      string               Yes E.g. completed, cancelled, refunded
  order_amount      decimal              Yes Monetary amount in dataset currency
  currency          string       Recommended ISO currency code
  discount_amount   decimal               No Discount amount
  shipping_amount   decimal               No Shipping amount
  item_count        integer               No Number of items

**Important data rules** - Currency must be consistent within an
analysis, or explicitly normalized before monetary comparisons. -
Cancelled and fully refunded orders must not be treated as completed
purchases. - Partial refunds require an explicit net-revenue policy. -
Timestamps must be parsed with an explicit timezone policy. - Customer
and order identifiers must be treated as strings, not numeric
quantities. - PII fields such as names, phone numbers, and email
addresses are not required for the MVP and should not be collected
unless a validated use case requires them.

### 7.3 Feature Engineering

The platform must generate reproducible customer-level features as of a
configurable analysis cutoff date.

Initial features: - `recency_days`: days since the customer's most
recent eligible purchase. - `frequency`: number of eligible orders in
the observation window. - `monetary`: total eligible net order value in
the observation window. - `average_order_value`: average eligible order
value. - `tenure_days`: days since customer creation or first eligible
order, according to a documented fallback policy. -
`days_between_orders_mean`: mean interval between eligible purchases,
where available. - `orders_last_30d`, `orders_last_90d`. -
`spend_last_30d`, `spend_last_90d`. - `discount_ratio`, where source
data is sufficient. - `refund_rate`, where refund data is sufficiently
reliable.

Requirements: - Feature definitions must be versioned. - Features must
be computed without using information from after the prediction
cutoff. - Missingness must be handled explicitly. - Data leakage checks
are mandatory. - Feature generation must be deterministic for the same
dataset and configuration.

### 7.4 Churn Prediction

#### Churn definition

Churn is not universally defined for all businesses. For the MVP, use an
explicit inactivity-based operational definition:

A customer is labeled as churned if they make no eligible purchase
during a configurable future label window after the observation cutoff,
provided the customer had enough prior history to be eligible.

Recommended initial defaults for an e-commerce pilot: - Observation
window: 180 days. - Label horizon: 90 days. - Eligible customer: at
least one eligible purchase before the cutoff. - Exclude customers whose
available history cannot cover the full label horizon.

These are configurable starting assumptions, not universal business
truths. They must be validated against purchase cycles and the target
merchant.

  -----------------------------------------------------------------------
  ID                      Requirement             Priority
  ----------------------- ----------------------- -----------------------
  CHURN-01                Churn label definition  P0
                          and windows are         
                          explicit and            
                          configurable.           

  CHURN-02                Prevent temporal        P0
                          leakage between         
                          features and labels.    

  CHURN-03                Train a baseline model  P0
                          and compare at least    
                          one stronger candidate. 

  CHURN-04                Use time-aware          P0
                          train/validation/test   
                          splits.                 

  CHURN-05                Report class balance    P0
                          and metrics including   
                          precision, recall, F1,  
                          ROC-AUC, and PR-AUC     
                          where valid.            

  CHURN-06                Support probability     P1
                          calibration assessment. 

  CHURN-07                Produce a churn         P0
                          probability for each    
                          eligible active         
                          customer.               

  CHURN-08                Convert probabilities   P0
                          to configurable risk    
                          bands.                  

  CHURN-09                Provide global and      P1
                          per-customer            
                          explanation signals.    

  CHURN-10                Track model artifact,   P0
                          feature version,        
                          dataset version, and    
                          configuration for every 
                          run.                    

  CHURN-11                Monitor prediction and  P1
                          feature drift.          
  -----------------------------------------------------------------------

Risk bands are presentation thresholds, not ground-truth classes.
Initial defaults may be: - Low: probability \< 0.30 - Medium:
0.30--0.69 - High: \>= 0.70

Thresholds must be configurable and should eventually be selected using
business capacity and intervention economics rather than arbitrary
defaults.

#### Model strategy

Start with: 1. Logistic Regression as an interpretable baseline. 2.
Random Forest or Gradient Boosting as a nonlinear benchmark. 3.
LightGBM/XGBoost only if dependency, licensing, data size, and
validation results justify them.

Do not select a model solely by accuracy. Churn datasets are often
imbalanced. Evaluate ranking quality, precision/recall trade-offs,
calibration, and business utility at the available intervention
capacity.

### 7.5 Customer Segmentation

Segmentation is descriptive and exploratory. Cluster IDs are not
inherently meaningful and must not be presented as natural or permanent
customer identities.

Initial segmentation inputs: - Recency - Frequency - Monetary value -
Average order value - Tenure - Recent order/spend trends, if reliable

Requirements:

  -----------------------------------------------------------------------
  ID                      Requirement             Priority
  ----------------------- ----------------------- -----------------------
  SEG-01                  Run segmentation on     P0
                          eligible customer-level 
                          features.               

  SEG-02                  Support K-Means as the  P0
                          initial algorithm.      

  SEG-03                  Standardize/transform   P0
                          features using a fitted 
                          preprocessing pipeline. 

  SEG-04                  Allow a bounded         P0
                          cluster-count selection 
                          or recommended          
                          candidate comparison.   

  SEG-05                  Report silhouette score P0
                          when mathematically     
                          applicable, plus        
                          cluster sizes.          

  SEG-06                  Profile each segment    P0
                          with medians, means,    
                          and distributions.      

  SEG-07                  Generate descriptive    P0
                          labels from measured    
                          characteristics, with   
                          user override.          

  SEG-08                  Persist model,          P0
                          preprocessing,          
                          parameters, and run     
                          metadata.               

  SEG-09                  Compare segment         P1
                          stability across runs.  

  SEG-10                  Support DBSCAN or other P2
                          algorithms after MVP    
                          validation.             
  -----------------------------------------------------------------------

Potential descriptive labels include: - High-value frequent customers -
Recent high-spend customers - At-risk / inactive customers - New
customers - Low-frequency customers

These labels must be assigned from actual segment profiles and must not
be hardcoded to cluster numbers.

### 7.6 Dashboard and Exploration

  -----------------------------------------------------------------------
  ID                      Requirement             Priority
  ----------------------- ----------------------- -----------------------
  UI-01                   Show dataset status and P0
                          latest analysis run.    

  UI-02                   Show eligible customer  P0
                          count and churn-risk    
                          distribution.           

  UI-03                   Show segment            P0
                          distribution and        
                          segment profiles.       

  UI-04                   Filter customers by     P0
                          risk band, segment, and 
                          available business      
                          attributes.             

  UI-05                   Provide a paginated     P0
                          customer table.         

  UI-06                   Show a customer detail  P0
                          view with relevant      
                          features and prediction 
                          metadata.               

  UI-07                   Show data-quality       P0
                          warnings and model      
                          evaluation metrics.     

  UI-08                   Compare analysis runs.  P1

  UI-09                   Support Arabic and      P1
                          English UI.             
  -----------------------------------------------------------------------

### 7.7 Export and Reporting

  -----------------------------------------------------------------------
  ID                      Requirement             Priority
  ----------------------- ----------------------- -----------------------
  EXP-01                  Export filtered         P0
                          customer predictions as 
                          CSV.                    

  EXP-02                  Export customer segment P0
                          assignments as CSV.     

  EXP-03                  Export a summary report P1
                          containing dataset,     
                          run, and model          
                          metadata.               

  EXP-04                  Apply organization      P0
                          permissions to all      
                          exports.                

  EXP-05                  Record export events in P1
                          audit logs.             
  -----------------------------------------------------------------------

### 7.8 API Requirements

Initial API capabilities: - Authentication and organization context. -
Dataset upload, status, validation report, and schema mapping. -
Analysis run creation and status. - Churn prediction results and
filters. - Segmentation results and profiles. - Customer-level detail. -
CSV exports. - Model/run metadata.

Long-running training and scoring must run asynchronously through a job
queue; HTTP requests must not remain open for full training jobs.

## 8. Non-Functional Requirements

### 8.1 Security and Privacy

-   Tenant isolation must be enforced server-side on every query and
    mutation.
-   Use secure password hashing and short-lived access tokens with a
    refresh strategy.
-   Enforce file size and file type limits.
-   Validate CSV content; never trust file extensions or client-provided
    MIME types.
-   Store uploaded files outside the public web root.
-   Use least-privilege service credentials.
-   Encrypt data in transit; use encrypted storage where supported.
-   Avoid collecting direct identifiers unless required.
-   Provide data deletion and retention policies.
-   Never expose raw customer data in application logs.
-   Apply rate limits to authentication, uploads, exports, and expensive
    analysis endpoints.

### 8.2 Reliability

-   Background jobs must be retry-safe and idempotent where practical.
-   Failed jobs must expose actionable error states.
-   A failed run must not overwrite a previous successful run.
-   Persist run configuration and artifact references.

### 8.3 Performance

Initial MVP targets, to be validated with a representative dataset: -
Standard dashboard API reads: p95 under 1 second, excluding large
exports. - Upload acknowledgement: under 3 seconds for normal-sized
files, with processing asynchronous. - Analysis execution: asynchronous;
progress and status are visible. - Pagination is mandatory for customer
lists. - Large exports must be generated asynchronously.

### 8.4 Maintainability

-   Separate ingestion, feature engineering, training, inference, and
    API layers.
-   Pin dependencies and version model artifacts.
-   Add automated tests for feature calculations and tenant boundaries.
-   Use structured logs and application metrics.

### 8.5 Accessibility and Usability

-   Clearly distinguish probability from certainty.
-   Explain risk bands and model limitations.
-   Use readable chart labels and accessible color contrast.
-   Provide downloadable templates and actionable validation errors.

## 9. Technical Architecture

### 9.1 Proposed stack

  -----------------------------------------------------------------------
  Layer                               Technology
  ----------------------------------- -----------------------------------
  Frontend                            Next.js / React

  Backend API                         FastAPI

  Database                            PostgreSQL

  Data processing                     pandas, NumPy

  ML                                  scikit-learn; evaluate
                                      LightGBM/XGBoost later

  Background jobs                     Celery + Redis, or an equivalent
                                      queue

  File storage                        S3-compatible object storage

  Model/artifact storage              Object storage with database
                                      metadata

  Deployment                          Docker; reverse proxy; managed or
                                      self-hosted infrastructure

  Observability                       Structured logs, metrics, error
                                      tracking
  -----------------------------------------------------------------------

The exact stack may be adjusted, but the boundaries between web
application, asynchronous processing, and ML pipelines should remain.

### 9.2 Logical flow

1.  User uploads CSV files.
2.  API stores files and creates a dataset record.
3.  Validation job inspects schema and data quality.
4.  User confirms mapping and starts an analysis run.
5.  Feature pipeline creates a versioned customer feature table.
6.  Churn pipeline trains/evaluates or loads an approved model and
    scores eligible customers.
7.  Segmentation pipeline fits preprocessing and clustering, then
    profiles segments.
8.  Results and metadata are persisted.
9.  Dashboard reads paginated results and aggregate summaries.
10. User exports a filtered audience or report.

### 9.3 Suggested backend domains

-   `identity`: users, organizations, roles, authentication.
-   `datasets`: uploads, schemas, validation, dataset versions.
-   `customers`: canonical customer records.
-   `orders`: canonical order records.
-   `features`: feature definitions and generated snapshots.
-   `churn`: labels, training runs, model registry, predictions.
-   `segmentation`: clustering runs, assignments, profiles.
-   `analytics`: dashboard aggregates and reports.
-   `jobs`: background task lifecycle.
-   `audit`: security-sensitive and business events.

## 10. Data Model (Conceptual)

Core entities: - Organization - User - Membership - Dataset -
DatasetFile - DatasetVersion - AnalysisRun - FeatureSnapshot -
ModelVersion - ChurnPrediction - SegmentationRun - Segment -
SegmentAssignment - ExportJob - AuditEvent

Every organization-owned record must carry an organization reference or
be reachable only through a strictly enforced organization-scoped parent
relation.

Important uniqueness constraints: -
`(organization_id, dataset_version_id, source_customer_id)` for
canonical customers within a dataset version. -
`(organization_id, dataset_version_id, source_order_id)` for canonical
orders within a dataset version. - `(analysis_run_id, customer_id)` for
predictions and assignments, as applicable.

## 11. ML Evaluation and Acceptance Criteria

### 11.1 Churn model acceptance

A model is not accepted merely because it trains successfully.

Required: - No feature/label temporal leakage. - Time-aware holdout
evaluation. - Baseline comparison. - Class balance report. - Precision,
recall, F1, ROC-AUC, and PR-AUC where applicable. - Confusion matrix at
the selected threshold. - Calibration assessment before treating
probabilities as reliable probabilities. - Performance at a realistic
top-k or intervention-capacity threshold. - Reproducible run metadata.

Business acceptance requires a pilot merchant to confirm that the ranked
customer list is useful and that acting on it can plausibly improve
retention economics.

### 11.2 Segmentation acceptance

Required: - Reproducible preprocessing and clustering configuration. -
Segment sizes and feature profiles. - Cluster quality metric where
applicable. - No empty or unusably tiny clusters without an explicit
warning. - Segment labels based on profile statistics. - A business
reviewer can explain the practical distinction between segments. -
Segment assignments can be exported and traced to a run.

### 11.3 MVP release acceptance

The MVP is releasable when: - A tenant can securely upload and validate
a dataset. - A complete analysis run can finish asynchronously. - Churn
results and segment assignments are persisted and viewable. - Users can
filter and export results. - Automated tests cover critical ML logic,
API permissions, and tenant isolation. - Error handling, logs, backup,
and deletion procedures are documented. - A pilot user can complete the
primary workflow without developer intervention.

## 12. Analytics and Product KPIs

### Product usage

-   Number of organizations onboarded.
-   Dataset upload completion rate.
-   Analysis run completion/failure rate.
-   Weekly active organizations.
-   Exports per organization.
-   Time from upload to first useful result.

### Model quality

-   Churn PR-AUC and recall at selected precision/capacity.
-   Precision among top-risk customers.
-   Calibration error.
-   Feature drift and prediction distribution drift.
-   Segment sizes and stability.

### Business outcomes (pilot measurement)

-   Retention rate among contacted high-risk customers versus an
    appropriate comparison group.
-   Incremental revenue or contribution margin retained, where
    measurable.
-   Campaign cost per retained customer.
-   Analyst hours saved.

Do not attribute retention improvements to the model without a valid
experimental or quasi-experimental measurement design.

## 13. Risks and Mitigations

  -----------------------------------------------------------------------
  Risk                    Impact                  Mitigation
  ----------------------- ----------------------- -----------------------
  Poor or inconsistent    Unreliable predictions  Validation report,
  merchant data                                   templates, minimum data
                                                  requirements

  Ambiguous churn         Misleading labels       Configurable business
  definition                                      definition and
                                                  documented assumptions

  Temporal leakage        Inflated offline        Time-based splits and
                          metrics                 leakage tests

  Class imbalance         Misleading accuracy     PR-AUC,
                                                  recall/precision,
                                                  threshold analysis

  Segments not actionable Low adoption            Profile-based
                                                  interpretation and
                                                  merchant review

  Small datasets          Unstable models         Minimum sample checks
                                                  and baseline warnings

  Data privacy exposure   Severe trust/legal risk Data minimization,
                                                  tenant isolation,
                                                  retention/deletion
                                                  controls

  Model drift             Degrading performance   Monitoring and
                                                  scheduled review

  No willingness to pay   Product failure         Customer interviews and
                                                  paid pilot validation
  -----------------------------------------------------------------------

## 14. Open Decisions

These decisions must be resolved during discovery or pilot setup: 1.
Which e-commerce platform or export format is the first target? 2. What
is the merchant's actual repeat-purchase cycle? 3. What inactivity
horizon defines churn for the pilot? 4. What is the minimum acceptable
history and customer count? 5. Which currencies and timezone rules are
supported first? 6. Is the first deployment shared SaaS or
single-tenant? 7. What is the retention period for uploaded raw files?
8. Which customer attributes may legally and operationally be processed?

## 15. Release Plan

### Release 0 --- Research and data contract

-   Interview prospective merchants.
-   Obtain a representative, permissioned dataset.
-   Confirm churn definition, schema, and commercial workflow.
-   Define baseline metrics.

### Release 1 --- ML proof of value

-   Build reproducible feature pipeline.
-   Evaluate churn baseline and segmentation.
-   Produce a clear analysis report.
-   Review outputs with a domain user.

### Release 2 --- Usable MVP

-   Authentication and tenant isolation.
-   CSV ingestion and validation.
-   Asynchronous analysis runs.
-   Dashboard, filters, and exports.
-   Logging, tests, and deployment.

### Release 3 --- Pilot hardening

-   Model monitoring.
-   Better explanations and run comparisons.
-   Merchant feedback and usability improvements.
-   Measure retention outcomes and willingness to pay.

## 16. Assumptions

-   Initial vertical is e-commerce, based on the current project
    direction.
-   Initial data source is CSV, not a live commerce integration.
-   Initial churn definition is inactivity-based and must be calibrated
    with the merchant.
-   Initial segmentation uses RFM-style behavioral features and K-Means.
-   The first version is decision-support software, not an autonomous
    marketing system.
-   Commercial viability is a hypothesis until validated through real
    customer discovery and a pilot.
