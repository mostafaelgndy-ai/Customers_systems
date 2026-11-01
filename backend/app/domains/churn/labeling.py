"""Churn label generation (CHURN-01 … CHURN-04).

Implements inactivity-based churn labeling using future outcome data
relative to a cutoff date. A customer is "churned" if they have no
eligible (completed) orders in the label horizon window after the
cutoff. Customers without a fully observable horizon are marked
ineligible (CHURN-02).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import pandas as pd

# Canonical column names
CUSTOMER_ID = "customer_id"
ORDER_ID = "order_id"
ORDER_DATE = "order_date"
ORDER_STATUS = "order_status"

# Eligible order status (completed purchases only)
ELIGIBLE_STATUSES = {"completed"}

# Churn label values
LABEL_CHURNED = "churned"
LABEL_ACTIVE = "active"
LABEL_INELIGIBLE = "ineligible"

# Defaults (CHURN-01 experimental defaults)
DEFAULT_OBSERVATION_WINDOW_DAYS = 180
DEFAULT_LABEL_HORIZON_DAYS = 90


@dataclass(frozen=True)
class LabelConfig:
    """Configuration for churn label generation. Immutable for reproducibility."""

    cutoff_date: str
    observation_window_days: int = DEFAULT_OBSERVATION_WINDOW_DAYS
    label_horizon_days: int = DEFAULT_LABEL_HORIZON_DAYS
    eligible_statuses: frozenset[str] = frozenset(ELIGIBLE_STATUSES)

    def observation_start(self) -> str:
        cutoff = datetime.fromisoformat(self.cutoff_date).replace(tzinfo=UTC)
        start = cutoff - timedelta(days=self.observation_window_days)
        return start.date().isoformat()

    def horizon_end(self) -> str:
        cutoff = datetime.fromisoformat(self.cutoff_date).replace(tzinfo=UTC)
        end = cutoff + timedelta(days=self.label_horizon_days)
        return end.date().isoformat()


def _eligible_orders(orders_df: pd.DataFrame):
    df = orders_df.copy()
    df[ORDER_DATE] = pd.to_datetime(df[ORDER_DATE], errors="coerce", utc=True).dt.tz_localize(None)
    mask = df[ORDER_STATUS].str.lower().isin({"completed"})
    return df.loc[mask & df[ORDER_DATE].notna()].copy()


def generate_labels(
    customers_df: pd.DataFrame,
    orders_df: pd.DataFrame,
    config: LabelConfig,
) -> list[dict[str, Any]]:
    """Generate churn labels for all customers (CHURN-01 to CHURN-03).

    Logic:
    - A customer is eligible for labeling only if they have at least one
      eligible order in the observation window (they are "active" during
      the observation period).
    - A customer is "churned" if they have NO eligible orders in the
      label horizon window after the cutoff.
    - A customer is "active" if they have at least one eligible order in
      the label horizon window.
    - Customers without any eligible orders in the observation window
      are "ineligible" (not enough history to label).

    All labeling uses only data on-or-before the cutoff for eligibility,
    and future data (after cutoff) only for the label outcome.
    """
    customers_df = customers_df.copy()
    customers_df[CUSTOMER_ID] = customers_df[CUSTOMER_ID].astype("string").str.strip()

    orders_df = orders_df.copy()
    orders_df[CUSTOMER_ID] = orders_df[CUSTOMER_ID].astype("string").str.strip()

    eligible_orders = _eligible_orders(orders_df)
    eligible_orders[CUSTOMER_ID] = eligible_orders[CUSTOMER_ID].astype("string").str.strip()

    cutoff = pd.Timestamp(config.cutoff_date)
    horizon_end = pd.Timestamp(config.horizon_end())
    obs_start = pd.Timestamp(config.observation_start())

    results: list[dict[str, Any]] = []

    for _, customer in customers_df.iterrows():
        cid = customer[CUSTOMER_ID]
        if not cid or cid == "nan":
            continue

        cust_orders = eligible_orders[eligible_orders[CUSTOMER_ID] == cid]

        # --- Eligibility: must have at least one eligible order in the
        #     observation window (CHURN-02) ----------------------------
        obs_orders = cust_orders[(cust_orders[ORDER_DATE] >= obs_start) & (cust_orders[ORDER_DATE] <= cutoff)]
        if obs_orders.empty:
            results.append(
                {
                    CUSTOMER_ID: cid,
                    "label": LABEL_INELIGIBLE,
                    "days_since_last_order": None,
                    "observation_window_days": config.observation_window_days,
                    "label_horizon_days": config.label_horizon_days,
                    "cutoff_date": config.cutoff_date,
                    "reason": "no eligible orders in observation window",
                }
            )
            continue

        # --- Days since last eligible order (on or before cutoff) -----
        last_order_date = obs_orders[ORDER_DATE].max()
        days_since_last_order = int((cutoff - last_order_date).days)

        # --- Label: future inactivity in label horizon (CHURN-03) -----
        horizon_orders = cust_orders[(cust_orders[ORDER_DATE] > cutoff) & (cust_orders[ORDER_DATE] <= horizon_end)]
        label = LABEL_CHURNED if horizon_orders.empty else LABEL_ACTIVE

        results.append(
            {
                CUSTOMER_ID: cid,
                "label": label,
                "days_since_last_order": days_since_last_order,
                "observation_window_days": config.observation_window_days,
                "label_horizon_days": config.label_horizon_days,
                "cutoff_date": config.cutoff_date,
                "reason": None,
            }
        )

    return results


def label_distribution(labels: list[dict[str, Any]]) -> dict[str, Any]:
    """Report positive/negative class counts and rates (CHURN-04).

    Returns counts and rates for churned, active, and ineligible classes,
    plus the churn rate among labeled (eligible) customers only.
    """
    total = len(labels)
    churned = sum(1 for row in labels if row["label"] == LABEL_CHURNED)
    active = sum(1 for row in labels if row["label"] == LABEL_ACTIVE)
    ineligible = sum(1 for row in labels if row["label"] == LABEL_INELIGIBLE)

    labeled_count = churned + active
    churn_rate = round(churned / labeled_count, 4) if labeled_count > 0 else None

    return {
        "total_customers": total,
        "churned": churned,
        "active": active,
        "ineligible": ineligible,
        "labeled_count": labeled_count,
        "churn_rate": churn_rate,
        "churn_rate_percent": round(churn_rate * 100, 2) if churn_rate is not None else None,
    }
