"""EDA report computation (FE-01).

Produces a deterministic, JSON-serialisable exploratory report for one
dataset version: row counts, date coverage, unique customers/orders,
missingness, duplicates, order statuses, and monetary distributions.

The report is recomputed from the version's immutable files on every
request — no caching step to invalidate, so the report is always
reproducible.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

CUSTOMER_ID = "customer_id"
ORDER_ID = "order_id"
ORDER_DATE = "order_date"
ORDER_STATUS = "order_status"
ORDER_AMOUNT = "order_amount"
CREATED_AT = "created_at"


def _parse_dates(series: pd.Series) -> pd.Series:
    return pd.to_datetime(series, errors="coerce", utc=True).dt.tz_localize(None)


def _missingness(df: pd.DataFrame, parse_dates: list[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for col in df.columns:
        missing = int(df[col].isna().sum())
        if col in parse_dates:
            # Blank/whitespace/malformed date strings count as missing too.
            parsed = _parse_dates(df[col])
            missing = max(missing, int(parsed.isna().sum()))
        if missing:
            counts[col] = missing
    return counts


def _duplicates(df: pd.DataFrame, id_col: str) -> dict[str, Any]:
    exact = int(df.duplicated().sum())
    duplicated_ids: list[str] = []
    if id_col in df.columns:
        counts = df[id_col].dropna().astype(str).value_counts()
        duplicated_ids = sorted(counts[counts > 1].index.tolist())
        return {"exact_duplicate_rows": exact, "ids": duplicated_ids}
    return {"exact_duplicate_rows": exact, "ids": []}


def _money_distribution(series: pd.Series) -> dict[str, Any]:
    values = pd.to_numeric(series, errors="coerce").dropna()
    if values.empty:
        return {
            "count": 0,
            "min": None,
            "max": None,
            "mean": None,
            "std": None,
            "median": None,
            "q25": None,
            "q75": None,
            "sum": None,
        }
    return {
        "count": int(values.count()),
        "min": round(float(values.min()), 2),
        "max": round(float(values.max()), 2),
        "mean": round(float(values.mean()), 4),
        "std": round(float(values.std()), 4) if len(values) > 1 else None,
        "median": round(float(values.median()), 4),
        "q25": round(float(values.quantile(0.25)), 4),
        "q75": round(float(values.quantile(0.75)), 4),
        "sum": round(float(values.sum()), 2),
    }


def _iso(value: pd.Timestamp | None) -> str | None:
    if value is None or pd.isna(value):
        return None
    return value.date().isoformat()


def compute_eda(customers_df: pd.DataFrame, orders_df: pd.DataFrame) -> dict[str, Any]:
    """Build the EDA report from canonicalized customers/orders frames."""
    customers_df = customers_df.copy()
    orders_df = orders_df.copy()

    order_dates = _parse_dates(orders_df.get(ORDER_DATE, pd.Series(dtype="object")))
    created_dates = _parse_dates(customers_df.get(CREATED_AT, pd.Series(dtype="object")))

    status_counts: dict[str, int] = {}
    if ORDER_STATUS in orders_df.columns:
        status_counts = {k: int(v) for k, v in orders_df[ORDER_STATUS].fillna("<blank>").value_counts().items()}

    unique_order_ids = int(orders_df[ORDER_ID].dropna().astype(str).nunique()) if ORDER_ID in orders_df.columns else 0
    unique_registered = (
        int(customers_df[CUSTOMER_ID].dropna().astype(str).nunique()) if CUSTOMER_ID in customers_df.columns else 0
    )
    orders_with_purchases = (
        int(orders_df[CUSTOMER_ID].dropna().astype(str).nunique()) if CUSTOMER_ID in orders_df.columns else 0
    )

    monetary_col = ORDER_AMOUNT if ORDER_AMOUNT in orders_df.columns else "amount"
    if monetary_col not in orders_df.columns:
        monetary = {
            "count": 0,
            "min": None,
            "max": None,
            "mean": None,
            "std": None,
            "median": None,
            "q25": None,
            "q75": None,
            "sum": None,
        }
    else:
        monetary = _money_distribution(orders_df[monetary_col])

    return {
        "row_counts": {
            "customers": int(len(customers_df)),
            "orders": int(len(orders_df)),
        },
        "unique_counts": {
            "customers_registered": unique_registered,
            "customers_with_orders": orders_with_purchases,
            "order_ids": unique_order_ids,
        },
        "date_coverage": {
            "first_order_date": _iso(order_dates.min() if not order_dates.dropna().empty else None),
            "last_order_date": _iso(order_dates.max() if not order_dates.dropna().empty else None),
            "first_customer_created_at": _iso(created_dates.min() if not created_dates.dropna().empty else None),
            "last_customer_created_at": _iso(created_dates.max() if not created_dates.dropna().empty else None),
        },
        "missing_values": {
            "customers": _missingness(customers_df, [CREATED_AT]),
            "orders": _missingness(orders_df, [ORDER_DATE]),
        },
        "duplicates": {
            "customers": _duplicates(customers_df, CUSTOMER_ID),
            "orders": _duplicates(orders_df, ORDER_ID),
        },
        "order_status_counts": status_counts,
        "monetary_distribution": monetary,
    }
