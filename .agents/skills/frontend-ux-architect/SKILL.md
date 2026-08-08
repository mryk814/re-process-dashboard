---
name: frontend-ux-architect
description: Structure this repository's user flows before implementing a new screen, redesign, navigation, onboarding, form, dashboard or Workbench, table, graph, visual editor, state surface, major CTA, result placement, or cross-screen handoff. Use a local mode for small interaction changes, structural mode for information architecture, and journey mode only for multi-screen decision flows. Do not trigger for typo fixes or isolated token replacement.
---

# Frontend UX Architect

Design the user's decision path before changing components or styling. Read [`verification-budget`](../../../docs/operations/verification-budget.md) and choose the smallest mode that matches the change.

## Shared authority

Read:

1. [`docs/product/ux-change-process.md`](../../../docs/product/ux-change-process.md).
2. [`docs/product/design-system.md`](../../../docs/product/design-system.md).
3. [`apps/web/AGENTS.md`](../../../apps/web/AGENTS.md).
4. The target component and its state owners.
5. Related API, Prediction Task, Capability, identity, and persistence contracts as needed.
6. The nearest relevant unit or E2E evidence.

Do not read every related document by default. Expand only when the change reaches that authority.

## Choose a mode

### local

Use when information order and the user journey remain unchanged.

Record only:

- the user question;
- the affected control／state;
- why the local placement is appropriate;
- one or two acceptance observations.

Verify only the changed state or interaction. Do not require two structural alternatives, every failure state, or a Scenario Journey.

### structural

Use when changing a screen, form structure, navigation, result placement, or handoff.

Write the UX Change Brief:

1. The question the user is trying to answer.
2. Information already fixed on arrival.
3. What this screen asks the user to decide.
4. What this screen must not ask the user to decide.
5. Information held in working memory.
6. Re-entry and cross-screen travel.
7. Recovery steps after an error.
8. Information that can be removed, merged, or deferred.
9. Structurally different cognitive models.
10. Why the chosen structure places each major element where it does.
11. Acceptance observations in the real screen.

Compare at least two genuinely different cognitive models unless one is clearly sufficient and the reason is stated. Color, spacing, radius, or left/right placement do not count as separate models.

### journey

Escalate for multi-screen decision flows involving onboarding, proposal, identity handoff, resume, or meaningful recovery.

Use the structural brief, then hand the prepared Project to the canonical [`scenario-journey-evaluator`](../../../.claude/skills/scenario-journey-evaluator/SKILL.md) after implementation. The Actor must not read implementation details or expected findings.

## Protect the decision evidence

- Reduce simultaneous decisions, not only visual clutter.
- Do not repair a structural problem only with copy or a tooltip.
- Separate safe defaults, business judgments, and reproducibility details.
- Do not ask for Dataset, Task, Package, Objective, or other context fixed upstream again.
- Put a warning before the value or action it changes.
- Show an error at the affected resource, including retained state and retry scope.
- Do not use an accessibility pass as proof of usability.
- Let the user's work order determine component structure, not the reverse.
- Do not copy an external design system's look.
- Never hide the distinction among prediction, actual, uncertainty, support, revision, Run, Snapshot, and scientific identity in the name of reducing cognitive load.

## Apply downstream external guidance narrowly

After selecting the structure, use only the relevant parts of:

- [`web-design-guidelines`](../../references/skills/web-design-guidelines/SKILL.md) for accessibility, interaction, forms, focus, and overflow.
- [`vercel-react-best-practices`](../../references/skills/vercel-react-best-practices/SKILL.md) for measured client-side React／Vite performance.
- [`vercel-composition-patterns`](../../references/skills/vercel-composition-patterns/SKILL.md) for component API shape without reducing the UX problem to props.

The repository design system remains the visual authority.

## Verification budget and stop rule

Declare the affected states before implementation. Verify those states, not the entire matrix automatically.

- local: nearest unit or interaction proof; fresh browser only when browser behavior changed.
- structural: normal path plus the directly affected loading／error／resume state.
- journey: representative end-to-end journey and only the relevant viewport／keyboard／recovery checks.

Stop when the acceptance observations are proven once on the current commit and no new UX hypothesis remains. Do not repeat the same journey through multiple runners only for confidence.
