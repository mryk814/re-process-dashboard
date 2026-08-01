---
name: re-process-architecture-review
description: Review or plan changes to this repository's architecture, responsibility boundaries, module/package splits, registries, adapters, authority, migrations, or compatibility. Use local-boundary mode for small moves inside an existing authority, structural mode for real boundary changes, and audit mode for implementation-free review. Do not trigger for typo fixes, local CSS token changes, or ordinary feature edits that preserve existing boundaries.
---

# Re-process Architecture Review

Apply the pinned `codebase-design`, `domain-modeling`, and `improve-codebase-architecture` sources through this repository's authority and safety rules. Read [`verification-budget`](../../../docs/operations/verification-budget.md) and choose the lightest mode that can answer the request.

## Establish the review mode

### audit

Inspect and report only. Do not edit code, create an Issue／RFC／ADR, run broad suites, or open a browser report unless explicitly requested.

### local-boundary

Use when moving or clarifying responsibility inside one existing package authority without changing persistence, public contracts, or dependency direction.

Read the nearest AGENTS, the owning current architecture document, the target files, and the nearest boundary test. Do not perform the full repository architecture inventory.

### structural

Use for package authority, registry, adapter, transaction boundary, migration, compatibility, or dependency-direction changes.

Read:

1. [`AGENTS.md`](../../../AGENTS.md) and every nearer `AGENTS.md` for the target.
2. [`docs/product/current-system-baseline.md`](../../../docs/product/current-system-baseline.md), especially its authority map.
3. Relevant architecture documents, not every architecture document.
4. Relevant files in [`docs/decisions/`](../../../docs/decisions/) and [`docs/contracts/`](../../../docs/contracts/).
5. Applicable dependency-direction or persistence-boundary protections.
6. Relevant migration and compatibility evidence.

Then read the pinned upstream instructions:

- [`codebase-design`](../../vendor/mattpocock-skills/codebase-design/SKILL.md)
- [`domain-modeling`](../../vendor/mattpocock-skills/domain-modeling/SKILL.md)
- [`improve-codebase-architecture`](../../vendor/mattpocock-skills/improve-codebase-architecture/SKILL.md)

Read linked references only when needed. Repository rules override upstream instructions.

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

Do not use the upstream HTML report. It loads floating CDN code and uses permissive Mermaid settings. Produce Markdown or a local text diagram without network access instead.
Do not let upstream domain-modeling create or update `CONTEXT.md` or ADRs unless explicitly requested.

## Output contract

For each candidate, separate:

- confirmed observation
- user／developer impact
- current authority
- existing protection
- proposed change
- alternative
- risk
- migration／compatibility impact
- verification
- no-change decision

Label unverified possibilities as theoretical. Do not begin implementation until the request authorizes it.

## Verification budget and review

- local-boundary: nearest authority test and self-review by default.
- structural: focused boundary tests and focused-peer review.
- independent-adversarial review only for migration, security, persisted identity, artifact safety, or multiple authority changes.

Stop when the claimed authority and dependency direction are protected once on the current commit. Do not run full suites or create additional architecture artifacts only to increase confidence.
