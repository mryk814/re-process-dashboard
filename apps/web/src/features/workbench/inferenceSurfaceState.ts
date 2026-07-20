export type InferenceSurfaceStatus = "latest" | "refreshing" | "stale" | "error";

export type InferenceSurfaceState<T> = Readonly<{
  data: T | null;
  currentIdentity: string | null;
  requestedIdentity: string | null;
  requestSequence: number;
  pending: boolean;
  error: unknown | null;
}>;

export function emptyInferenceSurface<T>(): InferenceSurfaceState<T> {
  return {
    data: null,
    currentIdentity: null,
    requestedIdentity: null,
    requestSequence: 0,
    pending: false,
    error: null,
  };
}

export function requestInferenceSurface<T>(
  state: InferenceSurfaceState<T>,
  identity: string,
): InferenceSurfaceState<T> {
  return {
    ...state,
    requestedIdentity: identity,
    requestSequence: state.requestSequence + 1,
    pending: true,
    error: null,
  };
}

export function resolveInferenceSurface<T>(
  state: InferenceSurfaceState<T>,
  requestSequence: number,
  identity: string,
  data: T,
): InferenceSurfaceState<T> {
  if (state.requestSequence !== requestSequence || state.requestedIdentity !== identity) return state;
  return {
    ...state,
    data,
    currentIdentity: identity,
    pending: false,
    error: null,
  };
}

export function rejectInferenceSurface<T>(
  state: InferenceSurfaceState<T>,
  requestSequence: number,
  identity: string,
  error: unknown,
): InferenceSurfaceState<T> {
  if (state.requestSequence !== requestSequence || state.requestedIdentity !== identity) return state;
  return { ...state, pending: false, error };
}

export function inferenceSurfaceStatus<T>(state: InferenceSurfaceState<T>): InferenceSurfaceStatus {
  if (state.error !== null) return "error";
  if (state.data !== null && state.currentIdentity !== state.requestedIdentity) return "stale";
  if (state.pending) return "refreshing";
  return "latest";
}
