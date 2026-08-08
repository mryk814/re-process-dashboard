# Internal Skill references

`.agents/references/` contains repository-controlled guidance that is not a
Codex discovery root. Public orchestration entries remain under
`.agents/skills/`; they reach these references through explicit relative links.

Do not add `agents/openai.yaml` here. External guidance remains pinned in
`.agents/vendor/` and is validated by `.agents/skill-inventory.json` without
executing vendor scripts, network actions, or writes.

The six moved references retain their repository safety wrappers here:

- `skills/codebase-design`
- `skills/domain-modeling`
- `skills/improve-codebase-architecture`
- `skills/web-design-guidelines`
- `skills/vercel-react-best-practices`
- `skills/vercel-composition-patterns`
