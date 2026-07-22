# Model Runtime examples by I/O contract

Start with the model's input/output representation, not its training-library name. These examples are inactive unless explicitly stated; their purpose is to freeze safe artifacts, adapter contracts, PredictiveSummary semantics, capability gaps, smoke, and quality evidence.

| I/O contract | Representative route | Status and card |
|---|---|---|
| fixed vector → deterministic scalar | `builtin.linear.v1` | production baseline; [existing runtime cards](existing-runtimes.md#fixed-vector--deterministic-scalar) |
| fixed vector → allow-listed sklearn estimator | `sklearn.skops.v1` | optional trusted-type runtime; [existing runtime cards](existing-runtimes.md#fixed-vector--allow-listed-sklearn-estimator) |
| fixed vector → native tree prediction | `lightgbm.booster.v1` | optional native runtime; [existing runtime cards](existing-runtimes.md#fixed-vector--native-tree-prediction) |
| fixed vector → parametric normal/lognormal | `builtin.exact_gp.v1`, `gpytorch.static_exact_rbf.v1` | production/example routes; [existing runtime cards](existing-runtimes.md#fixed-vector--parametric-normal-or-lognormal) |
| fixed vector → posterior predictive | `numpyro.dense_posterior.v1` | safe fixed dense graph; [existing runtime cards](existing-runtimes.md#fixed-vector--posterior-predictive) |
| fixed vector → shared multi-output artifact | PR #44 concept | design accepted, code not adopted; [decision card](shared-multi-output.md) |
| fixed vector → additive score + term contributions | `builtin.additive_terms.v1` | checked point/normal examples; [I/O card](additive-terms.md) |
| fixed vector → sparse posterior predictive | `builtin.posterior_linear.v1` | checked NumPyro-trained example; [I/O card](sparse-bayesian.md) |
| fixed vector → fixed empirical quantiles | `builtin.quantile_linear.v1` | checked quantile-only example; [I/O card](quantile-only.md) |
| fixed vector → binary/count/ordinal likelihood | `numpyro.dense_posterior.v1` | checked examples for all three target kinds; [I/O card](non-continuous-targets.md) |
| component predictive distributions → ensemble | no runtime | deferred by contract; [decision and design fixture](predictive-ensemble-decision.md) |

## Fast path for a new model request

1. Choose the nearest row by FeatureBundle shape, predictive representation, artifact type, and runtime dependency.
2. Reuse an existing runtime when the artifact can be exported without losing semantics. Do not add a runtime merely because the trainer library differs.
3. Copy the linked builder/fixture and state every unsupported semantic explicitly. Capability absence is not filled with a normal approximation, zero std, sorted crossing quantiles, or invented samples.
4. Run the example verifier before considering production activation:

```powershell
uv run python backend/scripts/verify_model_package.py <package-directory> --example
```

## Change-file map

When reusing a runtime, the expected scope is the builder/export script, checked Package, I/O card, adapter contract/smoke tests, and target-appropriate quality report. Do not edit the Registry, production TaskDefinition, or active selection.

Only a genuinely new safe artifact schema adds these application files:

- `backend/src/material_workbench/adapters/<adapter>.py`
- `RUNTIME_TYPES`, `PredictorSpec` architecture validation, and `AdapterRegistry` in `backend/src/material_workbench/model_packages.py`
- adapter contract and adversarial tests in `backend/tests/`
- the runtime table in `docs/model-package-contract.md`
- this index and one I/O card

If the new PredictiveSummary meaning is not already carried through API/snapshot/UI, update `Prediction`, regenerate OpenAPI/TypeScript types, and add one presentation contract test. Otherwise those surfaces are out of scope.

Production activation remains a separate, explicit decision. It additionally requires a real TaskDefinition match, dataset/profile provenance, task-specific quality review, lifecycle verification, and `models/active-packages.json` update. Example work must not create a fake production task, load trainer objects, add arbitrary Python plugins, build generic experiment tracking, or auto-activate every example.
