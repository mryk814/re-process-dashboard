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
- Objective Definition fixes what improvement means and which incumbent is used.
- Proposal Run stores the complete generated/evaluated pool, bounded rejection
  reasons, acquisition components and selected candidates.

The existing 範囲探索 screen remains the product surface. Proposal execution is
split into three allow-listed parts:

`Candidate Generator → Acquisition Evaluator → Selector`

- `latin_hypercube_v1` preserves the previous seeded LHS sequence and goal/
  shortfall ranking.
- `sobol_ucb_v1` uses a scrambled, seeded Sobol pool and UCB/LCB. The exploration
  coefficient is a saved request parameter rather than an internal constant.
- `sobol_ei_v1` additionally requires a fixed incumbent value. It records mean,
  predictive standard deviation, incumbent and expected improvement for every
  evaluated point.
- Thompson sampling, uncertainty sampling and support-boundary sampling have
  stable registry identities and capability requirements, but are deliberately
  marked unavailable until their Runtime representations are production-ready.

The API exposes availability and human-readable reasons. A requested unavailable
strategy is rejected unless the request explicitly permits deterministic
fallback; fallback changes the stored strategy identity and keeps
`fallback_from`.

Both LHS and Sobol take only the immutable Design Space as generation input:
fixed values, numeric and categorical domains, conditional inactive values, and
a balance component are applied before candidate validation. A
composition-total constraint with `balance_path` therefore produces an exact
remainder rather than relying on later rejection.

`screening-run/v6` pins Design Space and Objective digests, model/package,
Feature Pipeline and Dataset provenance, the actual generator/acquisition/
selector versions, seed, support policy, complete evaluated pool and selection
rank. Older screening runs remain readable without being rewritten.

### Experiment batch selection

An optional `batch-proposal-definition/v1` selects an experiment batch from the
saved acquisition-ranked shortlist. This is a separate selector, not a joint
acquisition function.

- `ranked_top_k_v1` is the explicit baseline.
- `greedy_value_diversity_v1` combines normalized rank utility with maximin
  distance. Every numeric distance is divided by its Design Space range;
  categorical differences are 0/1. Raw input scales are never mixed.
- Pending candidates can be avoided, penalized, or allowed. Their identities,
  policy and resulting exclusions remain in the run.
- A control reference selects the nearest feasible generated condition.
  Replicates repeat that planned condition; when promoted to the Candidate
  table, one Candidate condition represents the repeated observations.
- Category minimum/maximum quotas, per-candidate cost, total budget, setup-group
  limits and setup-change penalties are allow-listed contracts rather than
  Task-specific branches.
- Cluster representatives, local penalization, batch Thompson Sampling and a
  joint q-acquisition extension have stable registry identities but remain
  unavailable. The registry requires predictive samples for batch Thompson and
  joint samples for q-acquisition, so marginal scores cannot be mislabeled.

`batch-proposal-run/v1` stores selection order, role, reason, acquisition,
diversity, pending and resource components, excluded shortlist points, coverage,
pairwise diversity, estimated cost, selector version, seed and tie-break rule.
Only an explicit UI action promotes the selected unique conditions to ordinary
Candidates, whose screening provenance links back to the immutable parent run.

This remains the common generator boundary for future simplex samplers and
process-program decoders. It does **not** claim joint batch acquisition: each
point is scored marginally and the selector takes a deterministic top-k.

## Not in v1

- arbitrary Python, expressions, joins, or general ETL in Curation Recipe
- arbitrary heat-pattern generation
- a general Bayesian-optimization framework
- joint/batch acquisition
- training on unsafe MPEA fields merely to make a demo prediction available
