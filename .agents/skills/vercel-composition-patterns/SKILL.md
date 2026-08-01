---
name: vercel-composition-patterns
description: Explicitly review React 19 component APIs with boolean-prop proliferation, compound components, shared context, state ownership, or reusable variants. Do not use to turn a user-flow problem into a component framework.
---

# Vercel Composition Patterns

Read the pinned upstream
[`SKILL.md`](../../vendor/vercel-agent-skills/composition-patterns/SKILL.md), then load only relevant
rule files.

Use composition to clarify an already-selected UX structure. Do not reduce a UX problem to props,
introduce a framework merely to remove booleans, or blur ownership among server state, editing
drafts, URL state, and presentation metadata. Keep providers narrow and preserve the repository's
request identity and stale-result rules.
