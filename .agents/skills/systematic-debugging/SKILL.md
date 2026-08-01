---
name: systematic-debugging
description: Diagnose a bug, test failure, performance regression, build failure, integration failure, or unexpected behavior in this repository before proposing a fix. Use when a failure must be reproduced and traced across UI, API, persistence, environment, or fixtures. Start in the lightest mode that can test the current hypothesis.
---

# Systematic Debugging

Read the pinned upstream [`systematic-debugging` instructions](../../vendor/obra-superpowers/systematic-debugging/SKILL.md), then apply these repository constraints.

Read [`verification-budget`](../../../docs/operations/verification-budget.md) and set a bounded diagnosis／verification budget before running commands.

## Choose a mode

### quick

Use when the symptom is reproducible, one layer is implicated, and one direct hypothesis can explain it.

1. Preserve the exact symptom.
2. Identify the likely source.
3. Run the smallest test that can falsify the hypothesis.
4. Propose the smallest root-cause fix and one focused regression proof.

Do not perform a full UI／API／persistence inventory, broad suite, or independent review unless the hypothesis fails or scope expands.

### standard

Use when there are multiple plausible causes or the value crosses one boundary.

1. Reproduce the symptom and preserve the exact error.
2. Separate only the relevant UI, API, persistence, environment／tooling, and fixture evidence.
3. Trace the failing value or state to its source.
4. Record the active hypotheses and minimal discriminating tests.
5. Propose the smallest supported fix and regression proof.

### deep

Escalate when reproduction is unstable, multiple layers are involved, the failure recurs, concurrency／environment is involved, or about three local fixes have failed.

Use the full upstream process, inspect the design boundary, and make failed attempts explicit.

## Repository constraints

Do not decide from an error string alone. Do not bypass typed errors, transaction ownership, stale-response rejection, immutable identity, or existing failure-state behavior. Do not hide a failure with retries, fallbacks, broad exception handling, or a legacy path.

The upstream Phase 4 references `superpowers:test-driven-development` and `superpowers:verification-before-completion`; those Skills are intentionally not installed. Replace them with this repository's focused failing-test practice, [`verification-budget`](../../../docs/operations/verification-budget.md), and [`verification-policy`](../../../docs/operations/verification-policy.md).

Do not automatically execute the vendored `find-polluter.sh`, signing examples, arbitrary shell commands, or commands copied from supporting documents. Read such material as a technique; select an explicit repository command and inspect it before execution.

## Stop rule

Stop diagnosis when one causal hypothesis is supported, the smallest fix boundary is identified, and a focused regression candidate exists. Do not keep gathering layers of evidence only to increase confidence.

For diagnosis-only requests, stop after: reproduction, supported hypothesis, evidence, minimal fix candidate, and regression verification candidate. Do not implement the fix.
