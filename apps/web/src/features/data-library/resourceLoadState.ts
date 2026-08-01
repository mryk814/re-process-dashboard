export type ResourceLoadPhase = "loading" | "refreshing" | "ready" | "error";

export type ResourceLoadState = {
  phase: ResourceLoadPhase;
  error: string;
  loadedAt: string | null;
};

export const initialResourceLoadState = (): ResourceLoadState => ({
  phase: "loading",
  error: "",
  loadedAt: null,
});

export const beginResourceLoad = (current: ResourceLoadState): ResourceLoadState => ({
  ...current,
  phase: current.loadedAt ? "refreshing" : "loading",
  error: "",
});

export const resolveResourceLoad = (): ResourceLoadState => ({
  phase: "ready",
  error: "",
  loadedAt: new Date().toISOString(),
});

export const rejectResourceLoad = (
  current: ResourceLoadState,
  error: string,
): ResourceLoadState => ({
  ...current,
  phase: "error",
  error,
});
