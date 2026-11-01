"""CSV validation engine (DATA-09, DATA-10).

Implements the checks defined in docs/data-contract.md:
required columns, types, nulls, duplicates, numeric ranges, date parsing,
status vocabulary, currency consistency, and cross-file foreign keys.

Issue design: `error` blocks the version from becoming `valid`;
`warning` is surfaced but non-blocking. Row examples are capped, counts
bear the full truth. All output is JSON-serialisable (stored on the
dataset version).
"""

from __future__ import annotations

import datetime as dt
import re
from typing import Any

import pandas as pd

from app.domains.datasets.mapping import (
    CANONICAL_SCHEMAS,
    build_rename_map,
    normalize_header,
)

# Rows retained per issue before truncating to counts.
MAX_ISSUE_SAMPLES = 25
MAX_ISSUES_IN_REPORT = 200

_TZ_SUFFIX_RE = re.compile(r"(?:Z|z|[+-]\d{2}:?\d{2})\s*$")
DATE_FORMATS = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d")


class IssueCollector:
    """Accumulates issues with row samples capped per (code, field)."""

    def __init__(self) -> None:
        self._issues: dict[tuple[str, str, str], dict[str, Any]] = {}

    def add(
        self,
        *,
        code: str,
        severity: str,
        message: str,
        file_kind: str,
        field: str | None = None,
        rows: list[int] | None = None,
        count: int | None = None,
        extra: dict[str, Any] | None = None,
    ) -> None:
        key = (code, file_kind, field or "")
        issue = self._issues.setdefault(
            key,
            {
                "code": code,
                "severity": severity,
                "message": message,
                "file": file_kind,
                "field": field,
                "count": 0,
                "example_rows": [],
            },
        )
        issue["count"] += count if count is not None else len(rows or [])
        if rows:
            room = MAX_ISSUE_SAMPLES - len(issue["example_rows"])
            issue["example_rows"].extend(rows[:room])
        if extra and not issue.get("extra"):
            issue["extra"] = extra

    def to_list(self) -> list[dict[str, Any]]:
        return list(self._issues.values())

    @property
    def error_count(self) -> int:
        return sum(i["count"] for i in self._issues.values() if i["severity"] == "error")

    @property
    def warning_count(self) -> int:
        return sum(i["count"] for i in self._issues.values() if i["severity"] == "warning")


def _clean_string_columns(df: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    """Trim whitespace on identifier/text columns (internally, for checking only)."""
    for col in columns:
        if col in df.columns:
            df[col] = df[col].astype("string").str.strip()
    return df


def _required_check(df: pd.DataFrame, kind: str, issues: IssueCollector) -> None:
    wanted = set(CANONICAL_SCHEMAS[kind]["required"])
    present = {normalize_header(h) for h in df.columns}
    missing = sorted(wanted - present)
    if missing:
        issues.add(
            code="REQUIRED_COLUMN_MISSING",
            severity="error",
            message="Required field(s) missing after mapping: {}".format(", ".join(missing)),
            file_kind=kind,
        )


def _missing_value_check(df: pd.DataFrame, kind: str, required: list[str], issues: IssueCollector) -> None:
    for col in required:
        if col not in df.columns:
            continue
        mask = df[col].isna() | (df[col].astype("string").str.strip() == "")
        bad = list(df.index[mask])
        if bad:
            issues.add(
                code="MISSING_REQUIRED_VALUE",
                severity="error",
                message=f"'{col}' is empty on some rows.",
                file_kind=kind,
                field=col,
                rows=bad,
            )


def _duplicate_check(df: pd.DataFrame, dc_col: str, kind: str, issues: IssueCollector) -> None:
    if dc_col not in df.columns:
        return
    values = df[dc_col].astype("string").str.strip()
    mask = values.duplicated(keep="first") & values.notna() & (values != "")
    bad = list(df.index[mask])
    if bad:
        issues.add(
            code="DUPLICATE_ID",
            severity="error",
            message=f"Duplicate {dc_col} values; each row must be unique.",
            file_kind=kind,
            field=dc_col,
            rows=bad,
        )


def _numeric_check(
    df: pd.DataFrame, col: str, kind: str, issues: IssueCollector, *, required: bool
) -> pd.Series | None:
    """Parse a decimal column; register parse errors. Returns parsed Decimal series or None."""
    if col not in df.columns:
        return None
    raw = df[col].astype("string").str.strip()
    parsed = pd.to_numeric(raw.replace("", pd.NA), errors="coerce")
    non_empty = raw.notna() & (raw != "")
    bad_mask = non_empty & parsed.isna()
    bad = list(df.index[bad_mask])
    if bad:
        issues.add(
            code="INVALID_NUMBER",
            severity="error",
            message=f"'{col}' must be a decimal number.",
            file_kind=kind,
            field=col,
            rows=bad,
        )
    if required:
        _missing_value_check(df.assign(**{col: raw}), kind, [col], issues)
    return parsed


def _date_check(df: pd.DataFrame, col: str, kind: str, issues: IssueCollector) -> tuple[pd.Series | None, int, int]:
    """Parse dates; returns (parsed series | None, naive rows, aware rows)."""
    if col not in df.columns:
        return None, 0, 0
    raw = df[col].astype("string").str.strip()
    parsed = pd.to_datetime(raw.replace("", pd.NA), errors="coerce", format="mixed", utc=True)
    non_empty = raw.notna() & (raw != "")
    bad_mask = non_empty & parsed.isna()
    bad = list(df.index[bad_mask])
    if bad:
        issues.add(
            code="INVALID_DATE",
            severity="error",
            message=f"'{col}' must use ISO-8601, 'YYYY-MM-DD HH:MM:SS', or 'YYYY-MM-DD'.",
            file_kind=kind,
            field=col,
            rows=bad,
        )
    tz_aware = raw.str.contains(_TZ_SUFFIX_RE, na=False) & non_empty
    aware_rows = int(tz_aware.sum())
    naive_rows = int((non_empty & ~tz_aware).sum())
    return parsed, naive_rows, aware_rows


def validate_customers(df: pd.DataFrame, issues: IssueCollector) -> dict[str, Any]:
    metrics: dict[str, Any] = {"rows_total": int(len(df))}
    if metrics["rows_total"] == 0:
        issues.add(
            code="NO_DATA_ROWS",
            severity="warning",
            message="File has no data rows; check the source export.",
            file_kind="customers",
        )
    df = _clean_string_columns(df, ["customer_id", "country", "acquisition_channel", "customer_status"])
    _required_check(df, "customers", issues)
    _missing_value_check(df, "customers", list(CANONICAL_SCHEMAS["customers"]["required"]), issues)
    _duplicate_check(df, "customer_id", "customers", issues)
    metrics["distinct_customers"] = (
        int(df["customer_id"].astype("string").str.strip().nunique(dropna=True)) if "customer_id" in df.columns else 0
    )
    created_at, naive, aware = _date_check(df, "created_at", "customers", issues)
    metrics["created_at_naive_rows"] = naive
    metrics["created_at_aware_rows"] = aware
    if created_at is not None:
        metrics["created_at_min"] = str(created_at.min()) if created_at.notna().any() else None
        metrics["created_at_max"] = str(created_at.max()) if created_at.notna().any() else None
    return metrics


def validate_orders(df: pd.DataFrame, customer_ids: set[str] | None, issues: IssueCollector) -> dict[str, Any]:
    from app.domains.datasets.mapping import ORDER_STATUS_VOCABULARY

    required = list(CANONICAL_SCHEMAS["orders"]["required"])
    metrics: dict[str, Any] = {"rows_total": int(len(df))}
    if metrics["rows_total"] == 0:
        issues.add(
            code="NO_DATA_ROWS",
            severity="warning",
            message="File has no data rows; check the source export.",
            file_kind="orders",
        )
    df = _clean_string_columns(df, ["order_id", "customer_id", "order_status", "currency"])
    _required_check(df, "orders", issues)
    _missing_value_check(df, "orders", required, issues)
    _duplicate_check(df, "order_id", "orders", issues)

    # --- order_status vocabulary -------------------------------------------------
    if "order_status" in df.columns:
        raw_status = df["order_status"].astype("string").str.strip()
        known = raw_status.str.lower()
        non_empty = raw_status.notna() & (raw_status != "")
        unknown_mask = non_empty & ~known.isin(list(ORDER_STATUS_VOCABULARY.keys()) + [None])
        unknown = known[unknown_mask]
        if not unknown.empty:
            values = ", ".join(sorted(set(unknown.astype(str)))[:10])
            issues.add(
                code="INVALID_ORDER_STATUS",
                severity="error",
                message=f"Status value(s) outside the documented vocabulary: {values}",
                file_kind="orders",
                field="order_status",
                rows=list(unknown.index),
            )
        metrics["status_counts"] = {str(k): int(v) for k, v in known[non_empty].value_counts().items()}

    # --- monetary & numeric fields ------------------------------------------------
    amount = _numeric_check(df, "order_amount", "orders", issues, required=True)
    if amount is not None and "order_amount" in df.columns:
        negative = list(df.index[amount.notna() & (amount < 0)])
        if negative:
            issues.add(
                code="NEGATIVE_AMOUNT",
                severity="error",
                message="'order_amount' must be >= 0.",
                file_kind="orders",
                field="order_amount",
                rows=negative,
            )
        metrics["amount_mean"] = round(float(amount.mean()) if amount.notna().any() else 0, 4)
        metrics["amount_median"] = round(float(amount.median()) if amount.notna().any() else 0, 4)

    discount = _numeric_check(df, "discount_amount", "orders", issues, required=False)
    if discount is not None and amount is not None:
        exceeds = list(df.index[discount.notna() & amount.notna() & (discount > amount)])
        if exceeds:
            issues.add(
                code="DISCOUNT_EXCEEDS_AMOUNT",
                severity="warning",
                message="'discount_amount' is larger than 'order_amount'.",
                file_kind="orders",
                field="discount_amount",
                rows=exceeds,
            )

    shipping = _numeric_check(df, "shipping_amount", "orders", issues, required=False)
    if shipping is not None:
        negative = list(df.index[shipping.notna() & (shipping < 0)])
        if negative:
            issues.add(
                code="NEGATIVE_SHIPPING",
                severity="error",
                message="'shipping_amount' must be >= 0.",
                file_kind="orders",
                field="shipping_amount",
                rows=negative,
            )

    item_raw = df["item_count"].astype("string").str.strip() if "item_count" in df.columns else None
    if item_raw is not None:
        items = pd.to_numeric(item_raw.replace("", pd.NA), errors="coerce")
        non_empty = item_raw.notna() & (item_raw != "")
        bad = list(df.index[non_empty & items.isna()])
        if bad:
            issues.add(
                code="INVALID_NUMBER",
                severity="error",
                message="'item_count' must be a whole number.",
                file_kind="orders",
                field="item_count",
                rows=bad,
            )
        fractional = list(df.index[items.notna() & (items != items.round(0))])
        if fractional:
            issues.add(
                code="INVALID_NUMBER",
                severity="error",
                message="'item_count' must not contain fractions.",
                file_kind="orders",
                field="item_count",
                rows=fractional,
            )
        negative = list(df.index[items.notna() & (items < 0)])
        if negative:
            issues.add(
                code="NEGATIVE_ITEM_COUNT",
                severity="error",
                message="'item_count' must be >= 0.",
                file_kind="orders",
                field="item_count",
                rows=negative,
            )

    # --- currency consistency (DATA-04: never mix silently) -----------------------
    if "currency" in df.columns:
        cur = df["currency"].astype("string").str.strip()
        non_null = cur.dropna()
        non_null = non_null[non_null != ""]
        counts = {str(k): int(v) for k, v in non_null.str.upper().value_counts().items()}
        metrics["currency_counts"] = counts
        if len(counts) > 1:
            issues.add(
                code="MIXED_CURRENCIES",
                severity="error",
                message=(
                    f"Multiple currencies present ({', '.join(counts)}); the MVP "
                    "requires one currency per dataset version."
                ),
                file_kind="orders",
                field="currency",
                rows=list(non_null.index),
            )
        missing_cur = list(df.index[cur.isna() | (cur == "")])
        if missing_cur:
            issues.add(
                code="MISSING_CURRENCY",
                severity="warning",
                message="'currency' is recommended on every row.",
                file_kind="orders",
                field="currency",
                rows=missing_cur,
            )

    # --- dates & timezone policy ---------------------------------------------------
    order_dt, naive, aware = _date_check(df, "order_date", "orders", issues)
    metrics["date_naive_rows"] = naive
    metrics["date_aware_rows"] = aware
    if naive and aware:
        issues.add(
            code="MIXED_TIMEZONES",
            severity="error",
            message="The file mixes timezone-aware and naive timestamps; normalize to one convention.",
            file_kind="orders",
            field="order_date",
        )
    elif naive:
        issues.add(
            code="NAIVE_TIMESTAMPS",
            severity="warning",
            message="No timezone offsets present; naive timestamps are assumed UTC.",
            file_kind="orders",
            field="order_date",
            count=naive,
        )
    if order_dt is not None and order_dt.notna().any():
        metrics["date_min"] = str(order_dt.min())
        metrics["date_max"] = str(order_dt.max())
        future = list(df.index[order_dt.notna() & (order_dt > pd.Timestamp.now(tz="UTC") + pd.Timedelta(days=1))])
        if future:
            issues.add(
                code="FUTURE_DATES",
                severity="warning",
                message="Some order dates are in the future (beyond tolerable clock skew).",
                file_kind="orders",
                field="order_date",
                rows=future,
            )

    # --- foreign keys (cross-file; customer_ids from customers.csv) -----------------
    if customer_ids is not None and "customer_id" in df.columns:
        ids = df["customer_id"].astype("string").str.strip()
        fk_mask = ids.ne("") & ~ids.isin(customer_ids)
        fk_bad = list(df.index[fk_mask])
        if fk_bad:
            issues.add(
                code="FOREIGN_KEY_VIOLATION",
                severity="error",
                message="orders.customer_id values missing from customers.csv.",
                file_kind="orders",
                field="customer_id",
                rows=fk_bad,
            )
        metrics["distinct_customers_ordered"] = int(df["customer_id"].astype("string").str.strip().nunique(dropna=True))
    return metrics


def canonicalize(file_bytes: bytes, kind: str, mapping: dict[str, str] | None) -> pd.DataFrame:
    """Read raw file bytes and produce a canonicalized frame (phase-2 scope)."""
    df = pd.read_csv(
        pd.io.common.BytesIO(file_bytes),
        dtype=str,
        keep_default_na=False,
        na_filter=False,
    )
    df.columns = [normalize_header(str(c)) for c in df.columns]
    if mapping:
        rename = build_rename_map(mapping)
        if rename:
            df = df.rename(columns={normalize_header(s): c for s, c in rename.items()})
    return df


def run_validation(
    customers_bytes: bytes,
    orders_bytes: bytes,
    customers_mapping: dict[str, str] | None,
    orders_mapping: dict[str, str],
) -> dict[str, Any]:
    """Validate one dataset version; returns the persisted report payload."""
    issues = IssueCollector()
    customers_df = canonicalize(customers_bytes, "customers", customers_mapping)
    orders_df = canonicalize(orders_bytes, "orders", orders_mapping)

    customers_metrics = validate_customers(customers_df, issues)

    customer_ids: set[str] | None = None
    if "customer_id" in customers_df.columns:
        ids = customers_df["customer_id"].astype("string").str.strip()
        customer_ids = set(ids[ids != ""])

    orders_metrics = validate_orders(orders_df, customer_ids, issues)

    report: dict[str, Any] = {
        "generated_at": dt.datetime.now(dt.UTC).isoformat(),
        "status": "valid" if issues.error_count == 0 else "invalid",
        "summary": {"customers": customers_metrics, "orders": orders_metrics},
        "issue_counts": {"errors": issues.error_count, "warnings": issues.warning_count},
    }
    issue_list = sorted(
        issues.to_list(),
        key=lambda i: (0 if i["severity"] == "error" else 1, -i["count"]),
    )
    for issue in issue_list:
        # Row examples are reported as 1-based data-row numbers (header excluded).
        issue["example_rows"] = [row + 1 for row in issue["example_rows"]]
    report["issues"] = issue_list[:MAX_ISSUES_IN_REPORT]
    report["issues_truncated"] = len(issue_list) > MAX_ISSUES_IN_REPORT
    return report
