---
name: frontend-ux-architect
description: Structure this repository's user flows before implementing a new screen, redesign, navigation, onboarding, form, dashboard or Workbench, table, graph, visual editor, state surface, major CTA, result placement, or cross-screen handoff. Do not trigger for typo fixes, isolated CSS token replacement, or a visual-only adjustment that leaves information order and interaction structure unchanged.
---

# Frontend UX Architect

Design the user's decision path before changing components or styling.

## Read first

Read:

1. [`docs/product/ux-change-process.md`](../../../docs/product/ux-change-process.md).
2. [`docs/product/design-system.md`](../../../docs/product/design-system.md).
3. [`apps/web/AGENTS.md`](../../../apps/web/AGENTS.md).
4. The target component and its state owners.
5. Related API, Prediction Task, Capability, identity, and persistence contracts.
6. Related unit tests and E2E specifications.
7. The canonical [`scenario-journey-evaluator`](../../../.claude/skills/scenario-journey-evaluator/SKILL.md).

## Write the UX Change Brief before implementation

Record:

1. The question the user is trying to answer.
2. Information already fixed on arrival.
3. What this screen asks the user to decide.
4. What this screen must not ask the user to decide.
5. Information held in working memory.
6. Re-entry.
7. Cross-screen travel.
8. Scroll distance.
9. Recovery steps after an error.
10. Information that can be removed, merged, or deferred.
11. Structurally different cognitive models.
12. Why the chosen structure places each major element where it does.
13. Acceptance observations in the real screen.

For structural changes, compare at least two genuinely different cognitive models. Differences
limited to color, spacing, radius, or left/right placement do not count as separate models.

## Protect the decision evidence

- Reduce simultaneous decisions, not only visual clutter.
- Do not repair a structural problem only with copy or a tooltip.
- Separate safe defaults, business judgments, and reproducibility details.
- Do not ask for Dataset, Task, Package, Objective, or other context fixed upstream again.
- Put a warning before the value or action it changes.
- Show an error at the affected resource, including retained state and the retry scope.
- Do not use an accessibility pass as proof of usability.
- Let the user's work order determine component structure, not the reverse.
- Do not copy an external design system's look.
- Never hide the distinction among prediction, actual, uncertainty, support, revision, Run,
  Snapshot, and scientific identity in the name of reducing cognitive load.

## Apply downstream external guidance narrowly

After selecting the structure, use only the relevant parts of:

- [`web-design-guidelines`](../web-design-guidelines/SKILL.md) for accessibility, interaction,
  forms, focus, and overflow.
- [`vercel-react-best-practices`](../vercel-react-best-practices/SKILL.md) for measured
  client-side React/Vite performance.
- [`vercel-composition-patterns`](../vercel-composition-patterns/SKILL.md) for component API
  shape without reducing the UX problem to props.

The repository design system remains the visual authority.

## Verify after implementation

Use a fresh server and independent Workspace. Check the relevant normal path plus small viewport,
text enlargement, keyboard, loading, partial failure, unavailable capability, stale state,
back/forward, and resume. For a major decision flow, hand the prepared Project to
`$scenario-journey-evaluator` as an Actor that has not read the implementation or expected
findings.
