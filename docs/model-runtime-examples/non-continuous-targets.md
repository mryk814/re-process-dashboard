# Binary, count, and ordinal target I/O card

## When to use

Use these checked-in fixtures when a new model predicts a probability, a nonnegative count, or an ordered category rather than a continuous material property. They exercise the existing `numpyro.dense_posterior.v1` adapter without registering a fake production task or activating a Package.

## Contract matrix

| Kind | Predictive family | Point statistic | Required semantics | Fixture |
|---|---|---|---|---|
| binary | `bernoulli_logit` | probability | point and event probability agree and stay in `[0,1]`; dimensionless unit | `examples/model-packages/numpyro/bernoulli_logit` |
| count | `poisson_log`, `negative_binomial_log`, `zero_inflated_poisson_log` | rate | point and quantiles are nonnegative; count support is explicit | `examples/model-packages/numpyro/poisson_log` and peers |
| ordinal | `ordinal_logit` | expected category | finite increasing thresholds; unique ordered labels; outputs stay inside category index range | `examples/model-packages/numpyro/ordinal_logit` |

All examples share canonical inputs `composition.C` and `composition.Mn`, an ordered two-value FeatureBundle, and a safe NPZ containing only fixed dense-network posterior arrays. Training may use NumPyro/JAX; production inference uses NumPy only and never loads a trainer object, Python graph, pickle, or import path.

## Capability and presentation

- Binary exposes native event probability. Count and ordinal goal probability are unavailable unless a future contract defines the event.
- Quantiles come from deterministic seeded posterior-predictive evaluation. Raw samples are not exposed, so `samples=false`.
- Parametric family identity, target kind, point statistic, quantile levels, and ordered category metadata remain explicit.
- The UI renders binary points as percentages, ordinal points as expected categories, and never attaches a material unit to either. Missing goal probability is `利用不可`, not a fabricated normal approximation.
- Regression accuracy dashboards, confusion matrices, arbitrary categories, survival targets, and new production tasks are outside this fixture set.

## Build and verify without activation

```powershell
npm run models:build:examples
uv run python backend/scripts/verify_model_package.py examples/model-packages/numpyro/bernoulli_logit --example
uv run python backend/scripts/verify_model_package.py examples/model-packages/numpyro/poisson_log --example
uv run python backend/scripts/verify_model_package.py examples/model-packages/numpyro/ordinal_logit --example
```

Each Package includes hashed smoke input/expected output, a matching `TargetRuntimeCapability`, and a target-appropriate minimal quality metric. The commands do not modify `models/active-packages.json`.
