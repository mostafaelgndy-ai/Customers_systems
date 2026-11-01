# ml — Feature Engineering, Churn, and Segmentation Pipelines

Phases 3–5 of `docs/TASKS.md`. Kept structurally separate from the API layer
per PRD §8.4: ingestion, feature engineering, training, inference, and API
live in independent modules so pipelines can run from tests, workers, or
batch entrypoints without HTTP.

Planned layout:

```
ml/features/      reproducible customer feature pipeline (FE-01..10)
ml/churn/         labels, time-aware splits, models, scoring (CHURN-01..17)
ml/segmentation/  K-Means pipeline + profiles (SEG-01..09)
ml/evaluation/    metric reports, calibration, thresholds (CHURN-08..11)
```

Rules from the PRD that apply to every line of code here:

- Deterministic: same dataset version + config → identical outputs (FE-08).
- No post-cutoff information may touch features (CHURN-02, FE-09).
- Every artifact records feature version, dataset version, cutoff, label
  horizon, and dependency versions (CHURN-12).
- Metrics are reported with class balance; accuracy alone never selects a
  model (PRD §7.4).
