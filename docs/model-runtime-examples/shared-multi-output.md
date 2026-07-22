# Shared multi-output artifact decision card

## Decision on Draft PR #44

The I/O pattern is accepted as the representative design for multiple targets sharing one joint artifact. The implementation in [Draft PR #44](https://github.com/mryk814/re-process-dashboard/pull/44), pinned at code commit `7ee29f67bd2265b87f2509106115986e2d39db18`, is not adopted into current `main`.

As checked on 2026-07-22, the PR is draft, open, conflicting with the substantially newer task-driven workbench, and its code is not present in the runtime Registry. Therefore this repository must not advertise `builtin.multitask_gp.v1` as available.

## Accepted I/O pattern

- Canonical input → one ordered FeatureBundle.
- One safe joint artifact stores shared input/kernel state and an explicit target order.
- Multiple PredictorSpecs refer to that artifact with unique target indices.
- Every target returns its own PredictiveSummary while joint provenance remains shared.
- The Package is inactive until target-level quality and uncertainty trade-offs are accepted.

## Requirements before a current-main implementation

- Reuse bounded safe-NPZ loading; do not rely only on compressed artifact size.
- Verify precision/covariance positive definiteness instead of clipping negative predictive variance to zero.
- Store target order inside the artifact and validate each manifest target/index pair; uniqueness alone cannot detect swapped targets.
- Bind shared-artifact cache identity to Package digest and artifact digest.
- Preserve the existing TaskDefinition output set, Feature Pipeline order, per-target capability, snapshot provenance, and parent-condition-block quality comparison.
- Port the design as a new current-main implementation with focused contract tests. Do not cherry-pick the divergent PR wholesale.

The PR remains useful evidence for the shared-artifact shape and its TS/YS/EL/λ quality trade-off, but it is not a production dependency or executable example for Issue #45.
