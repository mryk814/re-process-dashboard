# Frontend boundaries

The web frontend is split by responsibility and uses one task-driven Workbench for every prediction task.

```text
app -> features/{projects,quality,lineage,screening,workbench,admin}
workbench -> candidates -> shared -> generated
```

- `app/` owns navigation intent and application-level provenance routing.
- `features/candidates/` owns the candidate model, task-driven input UI, and edit/save lifecycle. Consumers import from its public `index.ts`.
- `shared/api/` owns generated-client wrappers and request caching. It cannot depend on app or feature code.
- `features/workbench/` owns prediction surface state, selected-first preview loading, evidence, response curves, snapshots, and actuals.
- Other page features own their API-to-view transformations and feature-local styles.
- `app/App.tsx` owns routing and composition only; root `src` contains entry points and shared styling entry points, not domain modules.

## Enforcement

`npm run typecheck` runs `scripts/check-import-boundaries.mjs` before TypeScript. The checker rejects reverse dependencies, forbidden cross-feature imports, bypasses of feature public entries, dependency cycles, and root-level domain modules. It does not currently enforce file-size or CSS budgets; large-module reduction is handled by structural review and focused refactoring.

The common-flow browser contract is `e2e/shared-workbench.spec.ts`; inference invalidation and visible-surface request counts are fixed by `e2e/inference-p0.spec.ts`.
