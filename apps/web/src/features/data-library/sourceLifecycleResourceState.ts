export type SourceLifecycleResourcePhase =
  | "loading"
  | "ready"
  | "empty"
  | "stale"
  | "error";

export type SourceLifecycleResourceState = Readonly<{
  scope: string;
  phase: SourceLifecycleResourcePhase;
  loadedAt: string | null;
  error: string;
}>;

export function initialSourceLifecycleResourceState(
  scope: string,
): SourceLifecycleResourceState {
  return { scope, phase: "loading", loadedAt: null, error: "" };
}

export function beginSourceLifecycleResourceLoad(
  current: SourceLifecycleResourceState,
  scope: string,
): SourceLifecycleResourceState {
  if (current.scope !== scope) return initialSourceLifecycleResourceState(scope);
  return { ...current, phase: "loading", error: "" };
}

export function resolveSourceLifecycleResourceLoad(
  scope: string,
  empty = false,
  loadedAt = new Date().toISOString(),
): SourceLifecycleResourceState {
  return {
    scope,
    phase: empty ? "empty" : "ready",
    loadedAt,
    error: "",
  };
}

export function rejectSourceLifecycleResourceLoad(
  current: SourceLifecycleResourceState,
  scope: string,
  error: string,
): SourceLifecycleResourceState {
  if (current.scope === scope && current.loadedAt) {
    return { ...current, phase: "stale", error };
  }
  return { scope, phase: "error", loadedAt: null, error };
}
