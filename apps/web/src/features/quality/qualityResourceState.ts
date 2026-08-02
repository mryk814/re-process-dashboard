export type QualityResourcePhase =
  | "loading"
  | "ready"
  | "empty"
  | "stale"
  | "unavailable"
  | "error";

export type QualityResourceState = Readonly<{
  scope: string;
  phase: QualityResourcePhase;
  loadedAt: string | null;
  error: string;
}>;

export function initialQualityResourceState(scope: string): QualityResourceState {
  return { scope, phase: "loading", loadedAt: null, error: "" };
}

export function beginQualityResourceLoad(
  current: QualityResourceState,
  scope: string,
): QualityResourceState {
  if (current.scope !== scope) return initialQualityResourceState(scope);
  return { ...current, phase: "loading", error: "" };
}

export function resolveQualityResourceLoad(
  scope: string,
  empty: boolean,
  loadedAt = new Date().toISOString(),
): QualityResourceState {
  return { scope, phase: empty ? "empty" : "ready", loadedAt, error: "" };
}

export function rejectQualityResourceLoad(
  current: QualityResourceState,
  scope: string,
  error: string,
  unavailable: boolean,
): QualityResourceState {
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
