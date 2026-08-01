---
name: re-process-architecture-review
description: Review or plan changes to this repository's architecture, responsibility boundaries, module/package splits, registries, adapters, authority, migrations, or compatibility. Use before architecture audits or substantial structural changes; do not trigger for typo fixes, local CSS token changes, or ordinary feature edits that preserve existing boundaries.
---

# Re-process Architecture Review

Apply the pinned `codebase-design`, `domain-modeling`, and
`improve-codebase-architecture` sources through this repository's authority and safety rules.

## Establish the review mode

State whether the request is audit-only, design, or implementation.
For audit-only work, do not edit code, create an Issue/RFC/ADR, or open a browser report.
Only create those artifacts when the user explicitly requests them.

## Read the authority before judging structure

Read:

1. [`AGENTS.md`](../../../AGENTS.md) and every nearer `AGENTS.md` for the target.
2. [`docs/product/current-system-baseline.md`](../../../docs/product/current-system-baseline.md), especially its authority map.
3. [`docs/architecture/repository-structure-audit-2026-07-30.md`](../../../docs/architecture/repository-structure-audit-2026-07-30.md).
4. [`docs/architecture/persistence-transaction-boundaries.md`](../../../docs/architecture/persistence-transaction-boundaries.md).
5. [`docs/architecture/task-composition.md`](../../../docs/architecture/task-composition.md) and relevant architecture documents.
6. Relevant files in [`docs/decisions/`](../../../docs/decisions/) and [`docs/contracts/`](../../../docs/contracts/).
7. Applicable dependency-direction protections:
   - [`backend/tests/test_dependency_directions.py`](../../../backend/tests/test_dependency_directions.py)
   - [`backend/tests/test_persistence_boundaries.py`](../../../backend/tests/test_persistence_boundaries.py)
   - [`apps/web/scripts/check-import-boundaries.mjs`](../../../apps/web/scripts/check-import-boundaries.mjs)
8. Relevant migration and compatibility documentation, tests, Issues, PRs, and ADRs.

Then read the pinned upstream instructions:

- [`codebase-design`](../../vendor/mattpocock-skills/codebase-design/SKILL.md)
- [`domain-modeling`](../../vendor/mattpocock-skills/domain-modeling/SKILL.md)
- [`improve-codebase-architecture`](../../vendor/mattpocock-skills/improve-codebase-architecture/SKILL.md)

Read their linked references only when needed. Repository rules override upstream instructions.

## Review principles

- Do not infer an architecture smell from file length alone.
- Do not split a large module mechanically.
- First evaluate why a transaction owner, composition root, migration reader, or atomic use case is cohesive.
- Do not refactor only to make the code easier for AI to read.
- Separate confirmed friction from theoretical opportunity.
- Do not create a universal abstraction without a second use case or concrete change pressure.
- Do not add central branching by Task ID, material name, or model class.
- Do not create an arbitrary plugin framework.
- Do not rename persisted IDs, schemas, Workspaces, Packages, Runs, or Snapshots without evidence and a migration plan.
- Do not retain compatibility shims, old paths, or parallel V2 implementations casually.
- Preserve current authority and dependency direction.
- State impacts on APIs, persistence, scientific identity, migration, and compatibility.
- Do not treat fewer lines or more packages as success metrics.
- Permit a no-change decision when the current cohesion is stronger than the proposed split.

Do not use the upstream HTML report. It loads floating CDN code and uses permissive Mermaid
settings. Produce Markdown or a local text diagram without network access instead.
Do not let upstream domain-modeling create or update `CONTEXT.md` or ADRs unless explicitly
requested; this repository's current authority lives in the documents above.

## Output contract

For each candidate, separate:

- confirmed observation
- user/developer impact
- current authority
- existing protection
- proposed change
- alternative
- risk
- migration/compatibility impact
- verification
- no-change decision

Label unverified possibilities as theoretical. For a proposed change, name the smallest focused
gate that would protect authority and atomicity. Do not begin implementation until the request
authorizes it.
