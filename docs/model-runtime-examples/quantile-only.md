# Quantile-only predictor I/O card

## When to use

Use this route when the trained model returns fixed quantiles but no standard deviation, parametric distribution, or samples. The checked-in example is intentionally inactive and exists to prevent the API and UI from silently assuming a normal distribution.

## Contract

| Boundary | Value |
|---|---|
| Canonical input | `composition.x`, `process.scale` |
| FeatureBundle | ordered numeric vector `x`, `scale` |
| Runtime | `builtin.quantile_linear.v1` / `quantile_linear_v1` |
| Artifact | safe NPZ with `quantile_levels [Q]`, `coefficients [Q,F]`, `intercepts [Q]` |
| PredictiveSummary | median point estimate, original empirical quantiles, `predictive_family=empirical_quantiles` |
| Capability | quantiles true; std, samples, parametric distribution, components, goal probability false/unavailable |
| Training dependency | any quantile trainer that can export fixed coefficients; the fixture builder uses NumPy only |
| Runtime dependency | NumPy only |

The adapter requires unique ordered levels inside `(0, 1)`, a `0.5` level, exact feature width, finite arrays, and `crossing_policy=reject`. It rejects a crossing for the requested input instead of sorting predictions and hiding the model defect.

## Meaning and unsupported semantics

- `q05–q95` is a quantile interval, not a fitted normal 90% interval.
- The median is not relabeled as a mean.
- Quantiles are preserved in API responses and snapshots through `quantiles`, `point_statistic`, `target_kind`, and `predictive_family`.
- Standard deviation and goal probability are unavailable; the UI must not show `±0`, a normal approximation, or a perpetual calculation state.
- Arbitrary quantile counts, implicit CDF interpolation, distribution fitting, and calibration services are outside this example.

## Build and verify without activation

```powershell
npm run models:build:quantile-example
uv run python backend/scripts/verify_model_package.py examples/model-packages/quantile-linear --example
```

The quality report records per-quantile pinball placeholders for the known synthetic truth, interval coverage/width, and the crossing count over a fixed grid. `models/active-packages.json` is not changed.
