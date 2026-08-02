export type ProjectResourcePhase =
  | "loading"
  | "ready"
  | "empty"
  | "stale"
  | "unavailable"
  | "error";

export type ProjectResourceState = Readonly<{
  scope: string;
  phase: ProjectResourcePhase;
  loadedAt: string | null;
  error: string;
  unavailable: boolean;
}>;

export function initialProjectResourceState(scope: string): ProjectResourceState {
  return { scope, phase: "loading", loadedAt: null, error: "", unavailable: false };
}

export function beginProjectResourceLoad(
  current: ProjectResourceState,
  scope: string,
): ProjectResourceState {
  if (current.scope !== scope) return initialProjectResourceState(scope);
  return { ...current, phase: "loading", error: "", unavailable: false };
}

export function resolveProjectResourceLoad(
  scope: string,
  empty = false,
  loadedAt = new Date().toISOString(),
): ProjectResourceState {
  return {
    scope,
    phase: empty ? "empty" : "ready",
    loadedAt,
    error: "",
    unavailable: false,
  };
}

export function rejectProjectResourceLoad(
  current: ProjectResourceState,
  scope: string,
  error: string,
  unavailable = false,
): ProjectResourceState {
  if (current.scope === scope && current.loadedAt) {
    return { ...current, phase: "stale", error, unavailable };
  }
  return {
    scope,
    phase: unavailable ? "unavailable" : "error",
    loadedAt: null,
    error,
    unavailable,
  };
}
