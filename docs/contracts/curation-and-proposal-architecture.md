# Curation and Proposal architecture

## Data path

`Raw Snapshot → Curation Recipe → Dataset Input Profile → Canonical Dataset → Training Snapshot`

- Raw Snapshot is immutable.
- Dataset Input Profile owns source meaning, column mapping, entities and joins.
- Curation Recipe owns an allow-listed, versioned decision about whether a row is
  accepted, accepted with warning, quarantined, or blocked. It does not add joins
  or arbitrary expressions.
- Missing targets do not delete a row. Each output gets its own eligible cohort.
- Training Snapshot v2 records the selected row union, target-specific cohorts,
  grouped-split definition, and every group-to-fold assignment. Its digest does
  not contain a Feature Pipeline definition.
- Model Package owns the Feature Pipeline definition and digest, and links back
  to the Training Snapshot digest through provenance. Changing features creates
  a new Package without rewriting the Snapshot.
- Legacy Training Snapshot v1 payloads retain their original digest semantics;
  they are readable evidence, not inputs silently upgraded to v2.

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

LHS, Sobol and the allow-listed bounded-simplex sampler take only the immutable
Design Space as generation input:
fixed values, numeric and categorical domains, conditional inactive values, and
a balance component are applied before candidate validation. A
composition-total constraint with `balance_path` therefore produces an exact
remainder rather than relying on later rejection.

`bounded_simplex_goal_v1` is available only when one feasible balance constraint,
at least two continuous non-balance composition ranges, and no conditional
composition override are present. It uses seeded hit-and-run in the sum-zero
subspace, which remains practical for thin bounded polytopes without depending
on accept/reject volume. This targets uniform bounded-simplex coverage rather
than component-order-dependent stick breaking. The Run
stores generator ID/version/parameters,
per-variable generated coverage, validation rejection counts, and the independent
distance identity.

Distance is an allow-listed, versioned strategy rather than an unnamed
"materials distance":

- `scalar_axis_rms` is the generic baseline. Every declared scalar has equal
  weight after Design Space width normalization and categories use 0/1.
- `group_weighted_bounded_clr_rms` closes each constrained composition, applies
  zero-replaced centered log-ratio RMS and the pinned `d/(1+d)` bounding
  transform, then combines composition, process, category and heat groups with
  explicit weights. It deliberately does not claim to be the untransformed
  standard Aitchison distance. It is used by the bounded simplex strategy and
  saved in both Screening and Batch Run evidence.

The comparison spike deliberately keeps both. In the seeded two-component MPEA
case, independent LHS produced 113 negative-balance candidates out of 128;
bounded simplex produced 0 and preserved deterministic coverage evidence. A
symmetric four-component test also checks that no component receives the
first-allocation bias of sequential stick breaking.
The generic metric is still correct for scalar tasks, but the UI calls it
"各scalar軸の正規化RMS" rather than implying materials-science semantics.

The heat-program spike implements a strict `template_ramp_hold_cool` encoder and
decoder with exact round-trip evidence for ramp duration, peak temperature, hold
duration and cooling duration. Reheating, non-contiguous peaks and segment
boundaries are rejected as `heat_program_not_representable`; they are never
silently fitted. It remains a validated non-production decoder until a
candidate-level capability gate and semantic Design Space are wired through the
UI. Raw heat-point exploration therefore remains explicit rather than being
mislabelled as an engineering-program generator.

`screening-run/v7` pins the user's purpose (`design_space_map`, `goal_search`,
or `experiment_batch`) in addition to Design Space and Objective digests, model/package,
Feature Pipeline and Dataset provenance, the actual generator/acquisition/
selector versions, seed, support policy, complete evaluated pool and selection
rank. A Design Space map deliberately ignores a Project Objective and stores
reporting-only output metadata with support-distance evidence. An experiment
batch stores `source_run_id` and selects from that immutable goal-search pool;
it does not regenerate a second proposal. Older screening runs remain readable
without being rewritten, and their display purpose is inferred from goal and
batch evidence.

### Experiment batch selection

The `experiment_batch` purpose uses `batch-proposal-definition/v1` to select an experiment batch from an
explicitly sized acquisition-ranked prefix plus revision-pinned exact controls.
Its source must be a compatible saved `goal_search` Run. This is a separate
selector, not a joint acquisition function.

- `ranked_top_k_v1` is the explicit baseline.
- `greedy_value_diversity_v1` combines acquisition-rank utility with maximin
  distance using the proposal strategy's pinned distance ID/version/parameters.
  Distance is batch-selector evidence only; it does not affect proposal
  generation or acquisition ranking, and the UI says when a Run did not use it.
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
