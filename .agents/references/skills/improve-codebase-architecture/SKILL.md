---
name: improve-codebase-architecture
description: Explicitly scan a named area of this repository for confirmed architectural friction and deepening opportunities. Use only for an architecture audit or requested structural improvement, not ordinary feature work.
---

# Improve Codebase Architecture

Use the repository's `re-process-architecture-review` wrapper as the controlling
workflow, then read the pinned upstream
[`SKILL.md`](../../../vendor/mattpocock-skills/improve-codebase-architecture/SKILL.md) for its exploration
questions.

Do not generate or open the upstream HTML report: it loads floating Tailwind and Mermaid code from
external CDNs and uses permissive Mermaid settings. Use Markdown or a static local text diagram.
Do not create Issues, RFCs, ADRs, `CONTEXT.md`, or code changes unless the user asks for that
artifact. Do not require unavailable upstream `/grilling` commands. Separate confirmed friction,
theoretical opportunity, and no-change decisions.
