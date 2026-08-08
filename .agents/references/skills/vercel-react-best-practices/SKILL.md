---
name: vercel-react-best-practices
description: Explicitly review or optimize client-side React performance in this React 19 and Vite repository. Use when bundle, render, rerender, or waterfall cost has measured evidence or a clear code-level cause; do not trigger for routine component edits.
---

# Vercel React Best Practices

Read the pinned upstream
[`SKILL.md`](../../../vendor/vercel-agent-skills/react-best-practices/SKILL.md), then load only relevant
rule files.

Apply client-side React and framework-neutral JavaScript rules. Exclude Next.js, React Server
Components, Server Actions, `next/dynamic`, `after()`, server caching, server serialization, and
other server-specific guidance. Translate a useful dynamic-import rule to native `import()` or
`React.lazy` rather than copying Next.js syntax.

Do not optimize without measurement or a clear code path. Preserve request identity,
stale-response rejection, URL state, immutable revisions, and failure-state behavior. State the
evidence, expected effect, and verification metric before changing code.
