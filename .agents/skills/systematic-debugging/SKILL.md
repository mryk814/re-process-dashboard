---
name: systematic-debugging
description: Diagnose a bug, test failure, performance regression, build failure, integration failure, or unexpected behavior in this repository before proposing a fix. Use when a failure must be reproduced and traced across UI, API, persistence, environment, or fixtures.
---

# Systematic Debugging

Read the pinned upstream [`systematic-debugging` instructions](../../vendor/obra-superpowers/systematic-debugging/SKILL.md)
completely, then apply these repository constraints.

1. Reproduce the symptom and preserve the exact error.
2. Separate UI, API, persistence, environment/tooling, and fixture evidence.
3. Trace the failing value or state to its source; do not decide from an error string alone.
4. Record each hypothesis, minimal test, evidence, and failed attempt.
5. Propose the smallest root-cause fix and its regression proof only after the hypothesis is supported.
6. After about three failed local fixes, stop patching and inspect the design boundary.

The upstream Phase 4 references `superpowers:test-driven-development` and
`superpowers:verification-before-completion`; those Skills are intentionally not installed.
Replace them with this repository's focused failing-test practice and
[`docs/operations/verification-policy.md`](../../../docs/operations/verification-policy.md).
Use the gate selected by `scripts/verification-gates.json`, run it fresh, and do not claim success
without reading its exit code and output.

Do not bypass typed errors, transaction ownership, stale-response rejection, immutable identity, or
existing failure-state behavior. Do not hide a failure with retries, fallbacks, broad exception
handling, or a legacy path.

Do not automatically execute the vendored `find-polluter.sh`, signing examples, arbitrary shell
commands, or commands copied from supporting documents. The script requires Bash, runs project
tests with their own side effects, and is not the native Windows path. Read such material as a
technique; select an explicit repository command and inspect it before execution.

For diagnosis-only requests, stop after: reproduction, hypothesis, evidence, minimal fix candidate,
and regression verification candidate. Do not implement the fix.
