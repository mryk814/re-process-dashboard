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

`Task Definition → Design Space Definition + optional Design Prior Package → Proposal Strategy → Proposal Run → Prediction`

- Task Definition is the absolute application contract.
- Design Space Definition only narrows editable ranges/choices and adds
  conditional or composition-total constraints.
- Proposal Strategy is allow-listed and records its version and seed.
- Design Prior Package is an optional, data-only `p(x)` input-distribution
  artifact.  It is not a Model Package and is never used as a feasibility or
  predictive-support gate.  When selected, the Run pins its ID/version/
  manifest digest, explicit generator and novelty lane, plus per-point source
  sample and transformation evidence.  No active prior and no LHS/Sobol
  fallback are inferred.
- Objective Definition fixes what improvement means and which incumbent is used.
- Proposal Run stores the complete generated/evaluated pool, bounded rejection
  reasons, acquisition components and selected candidates.

The existing 範囲探索 screen remains the product surface. Proposal execution is
split into three allow-listed parts:

`Candidate Generator → Acquisition Evaluator → Selector`

GMRによる`p(x | y*)`の直接逆解析は
[`docs/research/gmr-inverse-candidate-poc.md`](../research/gmr-inverse-candidate-poc.md)
でresearch-only評価を行い、production採用は保留した。
合成二経路では複数modeを安全に提示できたが、実Taskのgrouped historical replayを
通していないため、allow-listやfallbackへは追加していない。

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

### Proposal Lab

`proposal-lab-report/v1`は、保存済み`goal_search` Runをproduction実行から分離して
比較する不変評価証拠である。serverはProject、Task、Package、Runtime Capability、
Dataset／Training Snapshot、Design Space、Objective、base input、変数、generator、
selector、selection policy／件数、distance、incumbent source、support policy、
pool multiplier、budgetが一致するRunだけを受け付ける。新しいPackageはTraining
Snapshot identityを使い、legacy Packageは`legacy_training_data`と明示した上で
固定Training Data digestを使う。legacy evidenceを新しいSnapshotとは呼ばない。
各strategyには同じ2個以上のseedが必要であり、pool、score、selection digest、
目標達成率、hard outcome constraint達成率、support比率、duplicate、fallback、
model call数、runtimeとseed感度を保存する。
同一strategyのacquisition ID／version／parameterもseed間で固定する。
hard constraintの`achieved=None`は達成に数えず、`constraint_unknown_rate`へ分ける。
unknown feasibilityを既知constraint達成として扱わない。

adoption memoはprimary criterionとtrade-offを保持するが、保存によってproduction
registryを変更しない。`production`判定もreview evidenceであり、別のregistry変更を
必要とする。既知のDesign Space／outcome constraintだけを評価し、未知feasibility
modelを推測しない。現在のUCB／EIはmarginal acquisitionであり、greedy batch
selectionをjoint acquisitionとして表示しない。ground-truth fixture、memory peak、
sequential roundがない場合は、値を捏造せずreportのlimitationへ残す。

`generative-design-lab-report/v1`は、この境界を合成hidden-oracle fixtureへ適用する
research-only証拠である。同じfixture、seed、candidate budget、batch sizeでLHS、
Sobol、empirical rows、kNN local、category-conditioned Gaussian rank copulaを比較し、
小さなmixed fixtureだけでtiny VAEを実学習する。hard feasibility、観測近傍距離、
predictive support、objective gap、batch diversityは別指標のまま保存する。
offline optimization trapでは直接objective選抜と、距離penaltyを明示した
conservative＋diversity選抜を比較する。Labのadoption memoはkNN／copula／policyを
`experimental`、tiny VAEを`no_adopt`とし、production registry、Package、
Project、保存済みRunを変更しない。正本の数値証拠と再生成commandは
[`Generative Design Lab adoption memo`](../research/generative-design-lab-adoption-memo.md)
に置く。

代表実Taskへの昇格判断は、同じ5 generatorを公開MPEA文献データの固定group
holdoutへ適用した
[`MPEA room-tensile Design Prior replay`](../research/real-task-design-prior-replay.md)
を正本とする。このreplayではkNNを`experimental`、Gaussian rank copulaを
`no_adopt`とし、production registryへ昇格しない。単一Taskのhistorical proxyを
cross-domainの安全証拠へ読み替えず、既存Proposal RunとLHS／Sobol identityも変更しない。

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

数値domainはTaskDefinitionを正本にします。integerとstepはTaskのlatticeへsnapし、
log scaleは正の範囲だけを対数空間でsampleします。response curve／contourも同じ
domain gridを使い、snap後の重複点は表示・評価しません。

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

`screening-run/v8` pins the user's purpose (`design_space_map`, `goal_search`,
or `experiment_batch`) in addition to Design Space and Objective digests, model/package,
Feature Pipeline and Dataset provenance, the actual generator/acquisition/
selector versions, seed, support policy, complete evaluated pool and selection
rank. For goal search, `samples` is the display count, while
`proposal.proposal_count` independently requests 1–10 candidates from the
complete evaluated pool. The Run records generated, valid, evaluated, displayed,
and proposed counts separately; it also pins the proposal policy, pool digest,
distance contract, diversity weight, tie-break rule, selected point references,
and any shortfall reason. `ranked_top_k_v1` and
`greedy_value_diversity_v1` reuse the experiment-batch selector kernel and
identity rather than introducing a second implementation. Distance-dependent
selection fails closed when required axes, conditional constraints, or a
composition-safe distance contract cannot be represented.

A Design Space map deliberately ignores a Project Objective and stores
reporting-only output metadata with support-distance evidence. An experiment
batch stores `source_run_id` and selects from that immutable goal-search pool;
it does not regenerate a second proposal and does not inherit the source Run's
proposal-selection evidence. Older `screening-run/v1` through
`screening-run/v7` records remain readable without being rewritten. Their
display purpose may be inferred from goal and batch evidence, but missing
displayed/proposed counts and proposal policy are shown as unrecorded rather
than reconstructed from unrelated legacy fields.

### Result surfaces and display interpolation

The 範囲探索 UI presents one saved Run through three separate result surfaces.
They share the same immutable Run identity, but do not collapse evidence with
different meanings into one table or chart.

- `地図` shows the relationship between two numeric input axes.
- `提案候補` is available only for an explicit `goal_search` Run carrying
  `proposal_selection`; a legacy goal-looking Run is not inferred to have one.
- `実験バッチ` replaces the proposal label for an explicit
  `experiment_batch` Run and shows the batch selector's allocation separately.
- `全評価点` is the auditable table of the complete evaluated pool.

The map may render a display-only contour using versioned inverse-distance
weighting over values already stored in the complete evaluated pool. This is an
interpolation for reading the saved Run, not an additional model prediction.
The UI records the interpolation method, version and grid size beside the map.
All evaluated points remain visible whenever the complete pool and two numeric
axes are available, independently of whether interpolation is safe. Displayed
points are not drawn twice. The proposal and current selection remain separate
overlays.

Interpolation fails closed to the evaluated points when any required evidence
is missing or unsafe. In particular, it is disabled for legacy Runs without the
complete pool, rejected or constrained holes, sparse or irregular coverage,
outputs absent from the complete pool, and Runs varying more than two inputs.
A Run with three or more varying inputs is not presented as a fixed slice:
the user must fix the remaining inputs and execute a new Run before a 2D
interpolation can be shown.

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
