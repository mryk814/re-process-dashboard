---
name: verification-budget-planner
description: Set a bounded verification and review plan before implementing or diagnosing a change in this repository. Use when deciding how much testing, browser work, review, or investigation is necessary; especially when a task feels smaller than the proposed validation effort. Do not use for data-only contributor work that does not change repository code.
---

# Verification Budget Planner

Read [`docs/operations/verification-budget.md`](../../../docs/operations/verification-budget.md) and [`docs/operations/verification-policy.md`](../../../docs/operations/verification-policy.md).

Before implementation or diagnosis, write a compact budget:

```yaml
change_class: micro | local | structural | critical
authority:
expected_scope:
verification_budget:
not_planned:
review: self | focused-peer | independent-adversarial
escalation_triggers:
stop_condition:
```

Choose the smallest class supported by the known change. Do not classify by file count or path alone.

## Defaults

### micro

Run one nearest direct check plus diff review. Do not run aggregate verification, full suites, browser journeys, or independent review by default.

### local

Run the focused unit／pytest and only the type or targeted browser evidence directly affected by the change. Use self-review.

### structural

Run focused tests for the changed authority, one representative journey when interaction changes, and relevant contract／architecture guards. Use focused-peer review when the boundary merits it.

### critical

Require compatibility, restart／rollback, or release evidence appropriate to the changed artifact, plus independent-adversarial review.

## Escalate only with evidence

Increase the budget only when:

- the first hypothesis fails;
- another authority is actually involved;
- persistence, security, migration, artifact safety, or scientific identity changes;
- focused evidence cannot observe shared state;
- a repeated regression or decision-safety risk is confirmed.

Record the added gate and reason. Do not expand because a broader check merely feels safer.

## Stop

Stop when the changed behavior or contract has been proven once on the current commit, the diff remains in scope, and no new causal hypothesis remains.

- Do not rerun a subset after a successful containing gate.
- Do not repeat the same gate on the same commit without changing the hypothesis, input, or environment.
- Reuse CI full-suite evidence for the same commit.
- Treat checkpoint follow-up as planned cross-cutting evidence, not as proof that the current implementation failed.
- Do not run release gates only to turn a planner status green when no release artifact changed.

If an unrelated failure appears, separate it unless it blocks the current proof.

Return the budget before running tests. If scope expands, revise the budget explicitly rather than silently accumulating verification.
