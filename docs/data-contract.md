# Data Contract (Canonical Input Schema)

**Applies to:** Phase 2 ingestion & validation (DATA-01 … DATA-05)\ 
Sources: PRD §7.2, §7.3 · Status: v1.0 draft-implementation baseline

The platform accepts CSV uploads. Users may upload files with any column
names, then map columns to the **canonical names** below. All analysis
(feature engineering, labels, training) uses only canonical names, so
the downstream pipeline never changes when raw headers change.

Since the MVP targets English-language markets, canonical names are
English. Multi-language column alias detection can be added later
without changing the contract.

---

## 1. `customers.csv` — one row per customer

| Canonical field        | Type     | Required | Semantics |
|------------------------|----------|----------|-----------|
| `customer_id`          | string   | **Yes**  | Stable unique identifier from the source system. Treated as a string, never a number (leading zeros / alphanumerics are meaningful). |
| `created_at`           | datetime | Recommended | Customer creation/registration timestamp in the source system. |
| `country`              | string   | No       | Country or market (ISO code or free text). |
| `acquisition_channel`  | string   | No       | Marketing source (e.g. `google`, `facebook`, `direct`). |
| `customer_status`      | string   | No       | Source-system status (e.g. `active`, `blocked`, `deleted`). Free text; passed through, not used for labels in the MVP. |

## 2. `orders.csv` — one row per order

| Canonical field     | Type    | Required | Semantics |
|---------------------|---------|----------|-----------|
| `order_id`          | string  | **Yes**  | Unique order identifier (string). |
| `customer_id`       | string  | **Yes**  | Foreign key to `customers.customer_id`. |
| `order_date`        | datetime| **Yes**  | Purchase timestamp. See timezone policy (§4). |
| `order_status`      | string  | **Yes**  | See status vocabulary (§3). |
| `order_amount`      | decimal | **Yes**  | Gross product total **before** discounts, **before** shipping, in dataset currency, 2 decimal places max. |
| `currency`          | string  | Recommended | ISO 4217 code (e.g. `USD`, `EGP`). See currency policy (§5). |
| `discount_amount`   | decimal | No       | Discount applied to the order, ≥ 0, ≤ `order_amount`. |
| `shipping_amount`   | decimal | No       | Shipping fees. Excluded from RFM monetary value by default. |
| `item_count`        | integer | No       | Number of purchased items, ≥ 0. |

**Extra columns** are allowed and preserved in the stored file, but are
ignored by the MVP analysis pipeline. Do **not** upload PII (names,
emails, phone numbers, addresses) — the MVP does not require or collect
direct identifiers (PRD §7.2 data rules).

## 3. Order status vocabulary & eligible-order policy (DATA-03)

Status values are parsed case-insensitively. Recognized values:

| Status                | Meaning                                  | Counts as eligible purchase? | Counts in monetary value? |
|-----------------------|------------------------------------------|------------------------------|---------------------------|
| `completed`           | Fulfilled, fully paid                    | **Yes**                      | **Yes**                   |
| `pending`             | Placed, not yet paid/shipped             | No                           | No                        |
| `processing`          | Being prepared                           | No                           | No                        |
| `shipped`             | Sent, not yet delivered                  | No                           | No                        |
| `cancelled`           | Cancelled before fulfilment              | No                           | No                        |
| `refunded`            | Paid then fully refunded                 | No                           | No                        |
| `partially_refunded`  | Paid then partially refunded             | **Yes** (net policy)         | *(P2 — see note)*         |
| `returned`            | Delivered then returned                  | No                           | No                        |

- **Eligible order** (default policy): `order_status == "completed"`.
- **Net revenue policy (P2):** partial refunds need a `net_revenue`
  decision before they can safely enter monetary features; the MVP
  treats them as eligible-for-recency but **excludes them from
  monetary value** until the pilot policy is confirmed.
- Unknown status values are reported per row as validation errors with
  the offending values listed; no row is silently "fixed".

## 4. Timezone policy (DATA-04)

- Timestamps must be **ISO‑8601** (`2025-03-14T10:22:00`) or the common
  log format `YYYY-MM-DD HH:MM:SS`, or date-only `YYYY-MM-DD`.
- Mixed naïve and timezone-aware values **within one file is an
  error** (it silently shifts windows). A uniform naïve file is
  accepted, with the assumed timezone recorded in the validation
  report (default assumption: **UTC** until an organization timezone
  is configured).
- Date-only timestamps are interpreted as UTC midnight and flagged as
  warnings (coarse recency).
- The dataset's minimum/maximum order dates are reported so cutoff
  windows can be sanity-checked before analysis.

## 5. Currency policy (DATA-04/§5)

- Within one dataset version, all non-null `currency` values must
  match. **Mixing currencies is a validation error** — the MVP does not
  normalize or convert; silent mixing would corrupt monetary features.
- Rows with null `currency` produce a warning (field is "Recommended").
  If *all* rows are null, a dataset-level currency may be declared later
  via analysis configuration (not part of Phase 2).

## 6. Duplicates, identifiers, missing values (DATA-05/06)

- Duplicate `customer_id` rows / duplicate `order_id` rows are
  **validation errors** listing affected rows (kept as raw data; the
  user fixes the source export).
- Missing values in required fields are row-level errors; missing
  optional fields are warnings only in aggregate.
- Monetaries: `order_amount < 0` is an error; `discount_amount >
  order_amount` is a warning; `item_count` must be a non-negative
  integer.
- Foreign key consistency: every `orders.customer_id` must exist in
  `customers.customer_id` of the same dataset version (cross-file
  check; both files required to create a version).

## 7. Validation severity model

- `error`: blocks the dataset version from becoming `valid`; shown
  with example row numbers (1-based, data rows, header excluded).
- `warning`: informational; does not block, but is surfaced on the
  validation summary (PRD §2.1: data quality problems are reported,
  never hidden).

## 8. Templates

Downloadable, validator-passing CSV templates are served by the API:

- `GET /api/v1/datasets/templates/customers`
- `GET /api/v1/datasets/templates/orders`

Template files live in `backend/templates/`.
