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
- `sobol_ucb_v1` uses a scrambled, seeded Sobol pool and UCB/LCB. The saved
  exploration parameter is the standard-deviation multiplier.
- `sobol_ei_v1` additionally requires a fixed incumbent value. It records mean,
  predictive standard deviation, incumbent, improvement margin `xi` and expected
  improvement for every evaluated point. Its saved parameter is `xi`, not the
  UCB multiplier.
- Thompson sampling, uncertainty sampling and support-boundary sampling have
  stable registry identities and capability requirements, but are deliberately
  marked unavailable until their Runtime representations are production-ready.

The API exposes availability and human-readable reasons. A requested unavailable
strategy is rejected unless the request explicitly permits deterministic
fallback; fallback changes the stored strategy identity and keeps
`fallback_from`.

UCB/LCB and EI require the typed `normal_mean_std` acquisition representation:
the Runtime output must declare a mean point statistic and predictive standard
deviation on an unconstrained continuous target. A median, probability, rate,
count, ordinal or positive-support target is not silently treated as a normal
mean. Each evaluated point records whether sigma
came from a named uncertainty component or from the central 90% interval normal
approximation, and the Run summarizes the methods actually used.

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

An optional `batch-proposal-definition/v1` selects an experiment batch from an
explicitly sized acquisition-ranked prefix plus revision-pinned exact controls.
This is a separate selector, not a joint acquisition function.

- `ranked_top_k_v1` is the explicit baseline.
- `greedy_value_diversity_v1` combines acquisition-rank utility with maximin
  distance. Every numeric distance is divided by its Design Space range;
  categorical differences are 0/1. Raw input scales are never mixed.
- Pending candidates can be avoided, penalized, or allowed. Their identities,
  policy and resulting exclusions remain in the run.
- A control request carries the Candidate revision visible when it was selected.
  The server rejects a stale revision, validates that exact condition against the
  run Design Space, and injects the pinned revision into the batch pool.
  It is never replaced by a nearest generated proxy. Replicates repeat the same
  pinned condition and are not promoted as duplicate Candidates.
- Conditions are deduplicated by a semantic digest of canonical Candidate
  inputs. The requested acquisition prefix size, exact-control count, duplicate
  count, unique count and pool digest are saved.
- Category minimum/maximum quotas, per-candidate cost, total budget, setup-group
  limits and setup-change penalties are allow-listed contracts rather than
  Task-specific branches.
- Cluster representatives, local penalization, batch Thompson Sampling and a
  joint q-acquisition extension have stable registry identities but remain
  unavailable. The registry requires predictive samples for batch Thompson and
  joint samples for q-acquisition, so marginal scores cannot be mislabeled.

`batch-proposal-run/v2` stores selection order, role, reason, acquisition-rank
utility,
diversity, pending and resource components, excluded shortlist points, coverage,
pairwise diversity, estimated cost, selector version, seed, tie-break rule and
candidate-pool evidence.
An impossible hard constraint is reported as `feasibility_infeasible`; exhaustion
of the deterministic greedy path is `greedy_search_exhausted` and is not described
as proof that no mathematical solution exists.
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
