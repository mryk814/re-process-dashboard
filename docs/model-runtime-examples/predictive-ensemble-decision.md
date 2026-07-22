# Predictive ensemble / BMA contract decision

## Decision

Do not add a predictive ensemble runtime in the current Model Package v1 contract.

Variable shrinkage/selection uncertainty belongs to the sparse Bayesian example, where one posterior artifact produces one predictive distribution. Predictive stacking/BMA instead combines already-complete component predictive distributions and requires component-level provenance. Treating these as the same feature would hide different uncertainty and validation assumptions.

## A/B comparison

### A. Refer to component predictors inside the same Package

This preserves reusable components but is rejected for v1. The current manifest has no typed component graph, component digest binding, target/unit/capability compatibility check, cycle/depth limit, recursive cache identity, or rule for missing optional dependencies. Adding only predictor IDs and weights would create a partially trusted recursive execution path.

### B. Export one fixed mixture artifact

This is the preferred future direction when a real use case justifies it. Training evaluates components and fixed weights, then exports one bounded, library-neutral artifact plus immutable provenance. Runtime complexity is lower, although component reuse decreases. The export must retain component Package/predictor IDs and digests, weight method, parent-block evaluation unit, CV settings, data digest, and code revision.

## Why implementation is deferred

- A mixture of normal distributions is not generally normal. Returning `predictive_family=normal` would be false.
- `PredictiveSummary` does not expose raw samples or a typed mixture family, so exact median, arbitrary quantiles, and event probability are not uniformly available across component capabilities.
- Snapshot and cache identity currently bind one Package, not an immutable component graph.
- Ignoring a missing or changed component is forbidden; a digest mismatch must reject the ensemble and require regeneration.
- The added complexity is not justified by a demonstrated quality gain. A single selected Package remains the production route; posterior model uncertainty within one sparse model is covered by the posterior-linear example.

## Design fixture

`examples/model-packages/design/predictive-mixture-v1.json` freezes the minimum future schema without registering a runtime. Its contract test verifies:

- finite nonnegative weights summing to one;
- common target and unit;
- full component digest binding;
- weight provenance;
- fixed-weight mean composition;
- exact 1/0 degeneration to a component;
- rejection after a component digest changes.

This fixture is a design golden, not an executable Model Package. Before implementation, add a bounded mixture artifact schema, a non-normal predictive representation, component provenance in snapshots/cache keys, scoring-rule evaluation on nested parent-condition splits, and adversarial tests for cycles, dependency absence, and degraded components.
