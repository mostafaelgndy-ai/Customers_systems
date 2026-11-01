"""Canonical schema, aliases, and mapping logic (DATA-01/02/08).

See docs/data-contract.md — this module is the executable side of that contract.
"""

from __future__ import annotations

CANONICAL_SCHEMAS: dict[str, dict[str, list[str]]] = {
    "customers": {
        "required": ["customer_id"],
        "optional": ["created_at", "country", "acquisition_channel", "customer_status"],
    },
    "orders": {
        "required": ["order_id", "customer_id", "order_date", "order_status", "order_amount"],
        "optional": ["currency", "discount_amount", "shipping_amount", "item_count"],
    },
}

# Common header spellings → canonical name. Matching is case/space-insensitive.
ALIASES: dict[str, dict[str, tuple[str, ...]]] = {
    "customers": {
        "customer_id": ("customer_id", "customerid", "cust_id", "id", "customer", "customer_key"),
        "created_at": ("created_at", "created", "created_date", "registered_at", "signup_date", "joined_at"),
        "country": ("country", "market", "country_code", "region"),
        "acquisition_channel": ("acquisition_channel", "channel", "source", "utm_source"),
        "customer_status": ("customer_status", "status", "account_status"),
    },
    "orders": {
        "order_id": ("order_id", "orderid", "order_number", "order_no", "id", "order_key"),
        "customer_id": ("customer_id", "customerid", "cust_id", "customer"),
        "order_date": ("order_date", "date", "purchase_date", "order_time", "ordered_at", "transaction_date"),
        "order_status": ("order_status", "status", "order_state", "state"),
        "order_amount": ("order_amount", "amount", "total", "order_total", "revenue", "gross_amount", "grand_total"),
        "currency": ("currency", "currency_code", "cur"),
        "discount_amount": ("discount_amount", "discount", "discount_value"),
        "shipping_amount": ("shipping_amount", "shipping", "shipping_cost", "ship_total"),
        "item_count": ("item_count", "items", "quantity", "qty", "num_items"),
    },
}

# ORDER STATUS VOCABULARY & ELIGIBLE POLICY mirror docs/data-contract.md §3
ORDER_STATUS_VOCABULARY: dict[str, str] = {
    "completed": "completed",
    "complete": "completed",
    "done": "completed",
    "pending": "pending",
    "awaiting_payment": "pending",
    "processing": "processing",
    "in_progress": "processing",
    "shipped": "shipped",
    "cancelled": "cancelled",
    "canceled": "cancelled",
    "refunded": "refunded",
    "partially_refunded": "partially_refunded",
    "part_refunded": "partially_refunded",
    "returned": "returned",
    "return": "returned",
}


def normalize_header(header: str) -> str:
    return header.strip().lower().replace(" ", "_").replace("-", "_").replace(".", "_")


def suggest_mapping(headers: list[str], kind: str) -> dict[str, str | None]:
    """Map headers to canonical names; unmatched canonical fields become None."""
    normalized = {normalize_header(h): h for h in headers}
    mapping: dict[str, str | None] = {}
    for canonical in list(CANONICAL_SCHEMAS[kind]["required"]) + list(CANONICAL_SCHEMAS[kind]["optional"]):
        found: str | None = None
        for alias in (canonical,) + ALIASES[kind].get(canonical, ()):
            if alias in normalized:
                found = normalized[alias]
                break
        mapping[canonical] = found
    return mapping


def validate_mapping(kind: str, mapping: dict[str, str]) -> None:
    """Raise ValueError when the mapping cannot produce the canonical schema."""
    spec = CANONICAL_SCHEMAS[kind]
    required_missing = [c for c in spec["required"] if not mapping.get(c)]
    if required_missing:
        raise ValueError("Mapping is missing required canonical field(s): {}".format(", ".join(required_missing)))
    allowed_canonical = set(spec["required"]) | set(spec["optional"])
    unknown = [c for c in mapping if c not in allowed_canonical]
    if unknown:
        raise ValueError("Unknown canonical field(s): {}".format(", ".join(sorted(unknown))))
    sources = [s for s in mapping.values() if s]
    if len(sources) != len(set(sources)):
        raise ValueError("The same source column is mapped to multiple canonical fields.")


def build_rename_map(mapping: dict[str, str]) -> dict[str, str]:
    """source -> canonical."""
    return {source: canonical for canonical, source in mapping.items() if source}
