export type LineageResourcePhase =
  | "loading"
  | "ready"
  | "empty"
  | "stale"
  | "unavailable"
  | "error";

export type LineageResourceState = Readonly<{
  scope: string;
  phase: LineageResourcePhase;
  loadedAt: string | null;
  error: string;
}>;

export function initialLineageResourceState(scope: string): LineageResourceState {
  return { scope, phase: "loading", loadedAt: null, error: "" };
}

export function beginLineageResourceLoad(
  current: LineageResourceState,
  scope: string,
): LineageResourceState {
  if (current.scope !== scope) return initialLineageResourceState(scope);
  return { ...current, phase: "loading", error: "" };
}

export function resolveLineageResourceLoad(
  scope: string,
  empty: boolean,
  loadedAt = new Date().toISOString(),
): LineageResourceState {
  return { scope, phase: empty ? "empty" : "ready", loadedAt, error: "" };
}

export function rejectLineageResourceLoad(
  current: LineageResourceState,
  scope: string,
  error: string,
  unavailable: boolean,
): LineageResourceState {
  if (current.scope === scope && current.loadedAt) {
    return { ...current, phase: "stale", error };
  }
  return {
    scope,
    phase: unavailable ? "unavailable" : "error",
    loadedAt: null,
    error,
  };
}
