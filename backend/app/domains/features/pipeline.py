"""Feature computation pipeline (FE-02, FE-03, FE-04, FE-05, FE-06, FE-08).

Implements deterministic customer-level behavioral features from validated
dataset versions. All functions are pure: same inputs always produce the
same outputs (FE-08). Temporal leakage is prevented by using only
on-or-before-cutoff data (FE-09).

Eligible orders (FE-02): only `order_status == "completed"` count toward
recency, frequency, and monetary value, per docs/data-contract.md §3.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import pandas as pd

# Canonical column names (docs/data-contract.md)
CUSTOMER_ID = "customer_id"
ORDER_ID = "order_id"
ORDER_DATE = "order_date"
ORDER_STATUS = "order_status"
ORDER_AMOUNT = "order_amount"
CREATED_AT = "created_at"

# Eligible order status (FE-02)
ELIGIBLE_STATUSES = {"completed"}

# Default risk-band thresholds (CHURN-14, reused here for consistency)
RISK_BANDS = {"low": (0.0, 0.30), "medium": (0.30, 0.69), "high": (0.69, 1.0)}

# Default observation/cutoff defaults (CHURN-01)
DEFAULT_OBSERVATION_DAYS = 180
DEFAULT_LABEL_HORIZON_DAYS = 90

# Feature schema version (FE-07) — bump when computation changes.
FEATURE_VERSION = "1.0.0"


@dataclass(frozen=True)
class FeatureConfig:
    """Configuration for a feature run. Immutable for reproducibility (FE-08)."""

    cutoff_date: str  # ISO date string, e.g. "2025-06-30"
    observation_days: int = DEFAULT_OBSERVATION_DAYS
    label_horizon_days: int = DEFAULT_LABEL_HORIZON_DAYS
    eligible_statuses: frozenset[str] = frozenset(ELIGIBLE_STATUSES)

    def observation_start(self) -> str:
        """ISO date string for the start of the observation window."""
        cutoff = datetime.fromisoformat(self.cutoff_date).replace(tzinfo=UTC)
        from datetime import timedelta

        start = cutoff - timedelta(days=self.observation_days)
        return start.date().isoformat()


def _parse_dates(series: pd.Series) -> pd.Series:
    """Parse mixed datetime formats into timezone-naive UTC timestamps."""
    return pd.to_datetime(series, errors="coerce", utc=True).dt.tz_localize(None)


def _eligible_orders(orders_df: pd.DataFrame, config: FeatureConfig) -> pd.DataFrame:
    """Filter to eligible orders within the observation window (FE-02)."""
    df = orders_df.copy()
    df[ORDER_DATE] = _parse_dates(df[ORDER_DATE])
    df[ORDER_AMOUNT] = pd.to_numeric(df[ORDER_AMOUNT], errors="coerce")

    cutoff = pd.Timestamp(config.cutoff_date)
    start = pd.Timestamp(config.observation_start())

    mask = (
        df[ORDER_STATUS].str.lower().isin({s.lower() for s in config.eligible_statuses})
        & df[ORDER_DATE].notna()
        & (df[ORDER_DATE] >= start)
        & (df[ORDER_DATE] <= cutoff)
    )
    return df.loc[mask].copy()


def compute_features(
    customers_df: pd.DataFrame,
    orders_df: pd.DataFrame,
    config: FeatureConfig,
) -> list[dict[str, Any]]:
    """Compute customer-level features (FE-03, FE-04, FE-05, FE-06).

    Returns a list of feature dicts, one per customer, keyed by
    customer_id. All values are JSON-serialisable.
    """
    # Normalize customer ids as strings
    customers_df = customers_df.copy()
    customers_df[CUSTOMER_ID] = customers_df[CUSTOMER_ID].astype("string").str.strip()

    # Eligible orders within the observation window
    eligible = _eligible_orders(orders_df, config)
    eligible[CUSTOMER_ID] = eligible[CUSTOMER_ID].astype("string").str.strip()

    cutoff = pd.Timestamp(config.cutoff_date)

    results: list[dict[str, Any]] = []

    for _, customer in customers_df.iterrows():
        cid = customer[CUSTOMER_ID]
        if not cid or cid == "nan":
            continue

        cust_orders = eligible[eligible[CUSTOMER_ID] == cid]

        # --- Recency (FE-03): days since last eligible order ----------
        if not cust_orders.empty:
            last_order_date = cust_orders[ORDER_DATE].max()
            recency_days = int((cutoff - last_order_date).days)
        else:
            recency_days = None  # no eligible orders in window

        # --- Frequency (FE-03): count of eligible orders --------------
        frequency = int(len(cust_orders))

        # --- Monetary (FE-03): sum of eligible order amounts ----------
        if not cust_orders.empty:
            monetary_value = float(cust_orders[ORDER_AMOUNT].sum())
            avg_order_value = float(cust_orders[ORDER_AMOUNT].mean())  # FE-04
        else:
            monetary_value = 0.0
            avg_order_value = 0.0

        # --- Tenure (FE-04): days from customer creation to cutoff ----
        created_at = _parse_dates(pd.Series([customer.get(CREATED_AT, "")]))[0]
        if pd.notna(created_at):
            tenure_days = int((cutoff - created_at).days)
            if tenure_days < 0:
                tenure_days = 0
        else:
            tenure_days = None

        # --- Rolling window features (FE-05) --------------------------
        orders_30d = cust_orders[cust_orders[ORDER_DATE] >= (cutoff - pd.Timedelta(days=30))]
        orders_90d = cust_orders[cust_orders[ORDER_DATE] >= (cutoff - pd.Timedelta(days=90))]
        orders_30d_count = int(len(orders_30d))
        orders_90d_count = int(len(orders_90d))
        monetary_30d = float(orders_30d[ORDER_AMOUNT].sum()) if not orders_30d.empty else 0.0
        monetary_90d = float(orders_90d[ORDER_AMOUNT].sum()) if not orders_90d.empty else 0.0

        # --- Purchase interval (FE-06) --------------------------------
        if frequency > 1 and not cust_orders.empty:
            sorted_dates = cust_orders[ORDER_DATE].sort_values()
            intervals = sorted_dates.diff().dropna().dt.total_seconds() / 86400.0
            avg_purchase_interval_days = float(intervals.mean()) if not intervals.empty else None
        else:
            avg_purchase_interval_days = None

        # --- Discount/refund exposure (FE-06) -------------------------
        discount_total = 0.0
        if "discount_amount" in cust_orders.columns:
            discount_total = float(pd.to_numeric(cust_orders["discount_amount"], errors="coerce").fillna(0).sum())

        results.append(
            {
                CUSTOMER_ID: cid,
                "recency_days": recency_days,
                "frequency": frequency,
                "monetary_value": round(monetary_value, 2),
                "avg_order_value": round(avg_order_value, 2),
                "tenure_days": tenure_days,
                "orders_30d": orders_30d_count,
                "orders_90d": orders_90d_count,
                "monetary_30d": round(monetary_30d, 2),
                "monetary_90d": round(monetary_90d, 2),
                "avg_purchase_interval_days": (
                    round(avg_purchase_interval_days, 2) if avg_purchase_interval_days is not None else None
                ),
                "discount_total": round(discount_total, 2),
                "cutoff_date": config.cutoff_date,
                "feature_version": FEATURE_VERSION,
            }
        )

    return results


def feature_schema() -> dict[str, dict[str, str]]:
    """Return the canonical feature schema (FE-07).

    Each feature has: name, definition, type, source, cutoff behavior,
    and missing-value policy.
    """
    return {
        "customer_id": {
            "definition": "Stable unique customer identifier from source system.",
            "type": "string",
            "source": "customers.customer_id",
            "cutoff": "none",
            "missing_policy": "error",
        },
        "recency_days": {
            "definition": "Days from cutoff to most recent eligible order. Null if no eligible orders.",
            "type": "integer|null",
            "source": "orders.order_date (eligible orders only)",
            "cutoff": "orders on/before cutoff only",
            "missing_policy": "null if no eligible orders",
        },
        "frequency": {
            "definition": "Count of eligible orders in the observation window.",
            "type": "integer",
            "source": "orders.order_id (eligible orders only)",
            "cutoff": "orders on/before cutoff only",
            "missing_policy": "0",
        },
        "monetary_value": {
            "definition": "Sum of eligible order amounts in the observation window (dataset currency).",
            "type": "decimal",
            "source": "orders.order_amount (eligible orders only)",
            "cutoff": "orders on/before cutoff only",
            "missing_policy": "0",
        },
        "avg_order_value": {
            "definition": "Mean eligible order amount in the observation window.",
            "type": "decimal",
            "source": "orders.order_amount (eligible orders only)",
            "cutoff": "orders on/before cutoff only",
            "missing_policy": "0",
        },
        "tenure_days": {
            "definition": "Days from customer creation date to cutoff. Null if creation date missing.",
            "type": "integer|null",
            "source": "customers.created_at",
            "cutoff": "none",
            "missing_policy": "null",
        },
        "orders_30d": {
            "definition": "Count of eligible orders in the 30 days before cutoff.",
            "type": "integer",
            "source": "orders.order_id (eligible orders only)",
            "cutoff": "orders in [cutoff-30d, cutoff]",
            "missing_policy": "0",
        },
        "orders_90d": {
            "definition": "Count of eligible orders in the 90 days before cutoff.",
            "type": "integer",
            "source": "orders.order_id (eligible orders only)",
            "cutoff": "orders in [cutoff-90d, cutoff]",
            "missing_policy": "0",
        },
        "monetary_30d": {
            "definition": "Sum of eligible order amounts in the 30 days before cutoff.",
            "type": "decimal",
            "source": "orders.order_amount (eligible orders only)",
            "cutoff": "orders in [cutoff-30d, cutoff]",
            "missing_policy": "0",
        },
        "monetary_90d": {
            "definition": "Sum of eligible order amounts in the 90 days before cutoff.",
            "type": "decimal",
            "source": "orders.order_amount (eligible orders only)",
            "cutoff": "orders in [cutoff-90d, cutoff]",
            "missing_policy": "0",
        },
        "avg_purchase_interval_days": {
            "definition": "Mean days between consecutive eligible orders. Null if fewer than 2 orders.",
            "type": "decimal|null",
            "source": "orders.order_date (eligible orders only)",
            "cutoff": "orders on/before cutoff only",
            "missing_policy": "null",
        },
        "discount_total": {
            "definition": "Sum of discount amounts on eligible orders in the observation window.",
            "type": "decimal",
            "source": "orders.discount_amount (eligible orders only)",
            "cutoff": "orders on/before cutoff only",
            "missing_policy": "0",
        },
    }
