# Existing Model Runtime I/O cards

## Fixed vector → deterministic scalar

- Canonical input becomes the task-specific ordered FeatureBundle.
- `builtin.linear.v1` reads `weights`, `bias`, and fixed interval offsets from safe NPZ.
- It returns a mean and empirical q05/q50/q95; it has no parametric distribution or native samples.
- Training and runtime dependencies are NumPy only. Arbitrary estimators, callbacks, and dynamic code are unsupported.
- Use `backend/scripts/build_default_model_package.py` and the production lifecycle verifier for the active Ridge baseline.

## Fixed vector → native tree prediction

- `lightgbm.booster.v1` reads a LightGBM native text asset, never a pickle or sklearn wrapper.
- The adapter returns a point-centered empirical summary. Std, samples, and parametric probability are unavailable unless a new explicit artifact contract is added.
- Training and runtime need LightGBM; missing optional dependency makes the Package unavailable rather than falling back to another model.
- Contract examples are in `backend/tests/test_optional_adapters.py`.

## Fixed vector → allow-listed sklearn estimator

- `sklearn.skops.v1` reads a skops artifact only for application-owned estimator-family and trusted-type allow-lists.
- It is suitable for fixed point predictors already expressible by those families; custom transformers, callbacks, arbitrary class graphs, and pickle/joblib remain forbidden.
- Training and runtime require the sklearn/skops optional dependency. Missing dependency makes the Package unavailable rather than selecting a fallback.
- The current summary is point-centered empirical quantiles; it does not imply parametric uncertainty.

## Fixed vector → parametric normal or lognormal

- `builtin.exact_gp.v1` reads bounded safe NPZ arrays for a fixed grouped exact-RBF architecture. `gpytorch.static_exact_rbf.v1` reads allow-listed safetensors for its fixed architecture.
- Normal outputs carry mean, std, q05/q50/q95, and uncertainty components. Lognormal is allowed only with the declared log1p latent transform and positive target semantics.
- Training may use general numerical/GP libraries. The built-in production runtime uses NumPy only; the GPyTorch route has explicit optional dependencies.
- Unknown tensor schema, non-finite values, incompatible shapes, and undeclared transforms are rejected. Trainer objects and arbitrary kernels are unsupported.
- Production builders and smoke live in `backend/scripts/build_default_model_package.py`, `build_hot_rolling_model_package.py`, and `build_flank_wear_model_package.py`.

## Fixed vector → posterior predictive

- `numpyro.dense_posterior.v1` evaluates a fixed dense MLP from posterior weight/bias arrays and an allow-listed likelihood.
- Safe NPZ limits entry count, expansion, compression ratio, tensor count, draws, layers, shape, and finite values.
- Normal/Student-t/lognormal, Bernoulli, Poisson/NB/ZIP, and ordinal likelihoods retain their target support and point statistic. Seeded posterior-predictive evaluation is deterministic.
- NumPyro/JAX is training-only; production inference uses NumPy. Raw samples are not exposed by PredictiveSummary.
- Checked smoke/quality/capability examples and verify commands are in `examples/model-packages/numpyro/` and [the non-continuous card](non-continuous-targets.md).
