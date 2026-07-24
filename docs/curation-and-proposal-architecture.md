# Curation and Proposal architecture

## Data path

`Raw Snapshot → Curation Recipe → Dataset Input Profile → Canonical Dataset → Training Snapshot`

- Raw Snapshot is immutable.
- Dataset Input Profile owns source meaning, column mapping, entities and joins.
- Curation Recipe owns an allow-listed, versioned decision about whether a row is
  accepted, accepted with warning, quarantined, or blocked. It does not add joins
  or arbitrary expressions.
- Missing targets do not delete a row. Each output gets its own eligible cohort.
- Training Snapshot records the Dataset/Profile/Recipe digests and grouped split.

The first executable recipe is embedded in the MPEA tabular profile and bundled
ridge baseline. Its row
result remains visible in `run_context.curation`; quarantined rows are retained,
with reasons, and quality issues summarize both quarantine and target missingness.

## Proposal path

`Task Definition → Design Space Definition → Proposal Strategy → Proposal Run → Prediction`

- Task Definition is the absolute application contract.
- Design Space Definition only narrows editable ranges/choices and adds
  conditional or composition-total constraints.
- Proposal Strategy is allow-listed and records its version and seed.
- Proposal Run stores accepted candidates and bounded rejection reasons before
  model evaluation.
- Objective Definition and acquisition functions are intentionally deferred.

The existing 範囲探索 screen remains the product surface. New runs persist their
validated Design Space, semantic digest, LHS strategy/seed and rejection summary
alongside the prediction result. The allow-listed LHS strategy now takes the
Design Space itself as input: fixed values, numeric and categorical domains,
conditional inactive values, and a balance component are applied before
candidate validation. A composition-total constraint with `balance_path`
therefore produces an exact remainder rather than relying on later rejection.

This is the common generator boundary for future simplex samplers, Bayesian
proposal strategies and process-program decoders. It does **not** yet make an
MPEA composition field generally editable: enabling that UI must be paired with
the corresponding total-composition contract, not independent numeric knobs.

## Not in v1

- arbitrary Python, expressions, joins, or general ETL in Curation Recipe
- arbitrary heat-pattern generation
- a general Bayesian-optimization framework
- acquisition-function selection
- training on unsafe MPEA fields merely to make a demo prediction available
