# Additive term model I/O card

## When to use

Use this route for GAM/EBM-like models whose prediction is a fixed intercept plus auditable additive terms. The runtime artifact is independent of the training library and the explanation is the model's local additive decomposition, not SHAP, partial dependence, or a causal effect.

## Contract

| Boundary | Value |
|---|---|
| Canonical input | `composition.x`, `categorical.route_code`, `process.z` |
| FeatureBundle | ordered numeric `x`, encoded `route_code`, `z` |
| Runtime | `builtin.additive_terms.v1` / `additive_terms_v1` |
| Artifact | safe NPZ containing scalar intercept and fixed arrays for each allow-listed term |
| Representative terms | two numeric `bspline_univariate` terms (including a nonlinear response) and one `categorical_lookup` |
| Link | identity only; explanation sum is on the same scale as the prediction |
| PredictiveSummary | point-only empirical family or an explicitly supplied normal approximation |
| Explanation | typed `AdditiveExplanation` with intercept, typed term contributions, link score, and prediction |
| Training dependency | builder uses NumPy least squares; another trainer may export the same arrays |
| Runtime dependency | NumPy only |

Each B-spline uses a fixed knot vector, degree 1–3, a positive-width domain, and constant-boundary extrapolation. Categorical values must match the exported numeric encoding exactly. Unknown kinds, fields, categories, non-finite tensors, degenerate knots, and incompatible shapes are rejected.

## Capability variants

- `point/` returns no quantiles, std, or goal probability. It must not acquire fabricated uncertainty.
- `normal/` contains a positive residual scale and returns mean/std/q05/q50/q95 with `predictive_family=normal`.
- Both variants return identical additive scores and explanations for the same FeatureBundle.
- Correlated inputs can make individual terms unstable. Contributions describe the fitted score and must not be presented as independent or causal effects.

The checked response-curve golden perturbs `composition.x`, maps canonical paths through the declared Feature Pipeline order, and evaluates the additive runtime. Pairwise interactions, arbitrary basis plugins, causal interpretation, SHAP, and trainer visualization objects are outside the first contract.

## Build and verify without activation

```powershell
npm run models:build:additive-examples
uv run python backend/scripts/verify_model_package.py examples/model-packages/additive-terms/point --example
uv run python backend/scripts/verify_model_package.py examples/model-packages/additive-terms/normal --example
```

The quality report records training RMSE, explanation reconstruction error, and response-curve span over fixed synthetic data. Neither Package is added to `models/active-packages.json`.
