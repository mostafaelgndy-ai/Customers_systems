"""Feature engineering domain (Phase 3 — FE-01 … FE-10).

Computes customer-level behavioral features (RFM, tenure, AOV, rolling
windows) from validated dataset versions. Results are persisted as
immutable feature snapshots linked to a dataset version, organization,
cutoff date, and feature version.
"""
