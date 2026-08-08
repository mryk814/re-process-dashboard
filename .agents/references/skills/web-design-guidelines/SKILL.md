---
name: web-design-guidelines
description: Explicitly audit this repository's UI code for accessibility, interaction, form, focus, overflow, and resilient-state issues after the information structure is established. Do not use as a visual redesign generator.
---

# Web Design Guidelines

Read the vendored upstream [`SKILL.md`](../../../vendor/vercel-agent-skills/web-design-guidelines/SKILL.md)
for provenance, but do **not** follow its instruction to fetch mutable `main`.
Use the pinned local
[`command.md`](../../../vendor/vercel-web-interface-guidelines/command.md) snapshot instead.

Treat [`docs/product/design-system.md`](../../../../docs/product/design-system.md) as the visual
authority. Use the external rules for accessibility, interaction, forms, focus, overflow, loading,
and failure-state review. Do not copy its aesthetics or use it to override scientific evidence,
identity, or information order. Report findings with repository-relative `file:line`, severity,
user impact, and a concise remediation. Do not edit code during an audit-only request.
