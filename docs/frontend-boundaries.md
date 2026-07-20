# Frontend boundaries

The web frontend is being split by responsibility without changing behavior.

```text
app -> features/workbench -> features/candidates -> shared -> generated
```

- `app/` owns navigation intent and application-level provenance routing.
- `features/candidates/` owns the candidate model, task-driven input UI, and edit/save lifecycle. Consumers import from its public `index.ts`.
- `shared/api/` owns generated-client wrappers and request caching. It cannot depend on app or feature code.
- Root `src` modules are limited to the current composition entry points while `App.tsx` is incrementally decomposed.

`npm run typecheck` runs `scripts/check-import-boundaries.mjs` before TypeScript. The checker prevents reverse dependencies, cross-feature dependencies from candidates, imports that bypass the candidate public entry, and new root-level domain modules.

## Remaining work for #15

After the shared prediction workbench replaces the separate hot-rolling screen, continue by extracting page state and API transformations into their owning features, colocating feature CSS without selector changes, and reducing `App.tsx` to routing and application composition. Browser and visual baselines must be compared during that phase.
