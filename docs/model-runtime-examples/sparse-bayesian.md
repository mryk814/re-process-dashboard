# Sparse Bayesian posterior I/O card

## When to use

Use this route when coefficient shrinkage uncertainty and posterior-predictive uncertainty must survive export without loading a PyMC/NumPyro object in production. This example keeps variable-selection reporting separate from predictive responses and from predictive ensemble/BMA.

## Contract

| Boundary | Value |
|---|---|
| Canonical input | `composition.x0` through `composition.x7` |
| FeatureBundle | the same eight numeric features in fixed order |
| Runtime | `builtin.posterior_linear.v1` / `posterior_linear_v1` |
| Artifact | safe NPZ: `beta_draws [D,F]`, `intercept_draws [D]`, positive `noise_scale_draws [D]`, optional positive `local_scale_draws [D,F]` or binary `indicator_draws [D,F]` |
| PredictiveSummary | mean, empirical q05/q50/q95, posterior-predictive std and epistemic/aleatoric components |
| Capability | quantiles/std/components true; samples and parametric distribution false; goal probability unavailable |
| Training dependency | NumPyro/JAX horseshoe in the builder |
| Runtime dependency | NumPy only |

Draw count, feature width/order, finite values, positive noise/local scales, binary indicators, and exact tensor schema are checked before inference. A seed controls observation-noise sampling, making the smoke deterministic. Raw draws are not exposed by `PredictiveSummary`, so `samples=false` even though the artifact stores posterior draws.

## Selection and quality reports

`reports/selection-report.json` contains coefficient mean/sd/quantiles, sign probability, ROPE-outside probability, local-scale mean, and the declared selection rule. Horseshoe shrinkage is continuous: the report does not call these values inclusion probabilities and does not delete unselected Feature Pipeline inputs.

The fixture deliberately contains two signals, six noise features, correlated signal candidates, and small parent-condition blocks. For every held-out parent block, `quality-report.json` trains a full horseshoe on the remaining blocks, applies the ROPE selection rule inside that training fold, retrains the selected reduced horseshoe, and reports both held-out RMSE values. A full-feature ridge score is retained only as a separate baseline.

Coefficient/shrinkage summaries are not causal importance. Correlated features may share posterior evidence. Arbitrary Bayesian graphs, runtime JAX/PyTensor restoration, automatic feature removal, UI prior editing, and all-subset BMA are outside this contract.

## Build and verify without activation

```powershell
npm run models:build:posterior-linear-example
uv run python backend/scripts/verify_model_package.py examples/model-packages/posterior-linear --example
```

The build command trains with the optional NumPyro dependency, exports numeric arrays, and verifies them through the NumPy production adapter. It does not modify `models/active-packages.json`.
