export type InferenceCacheStats = Readonly<{
  hits: number;
  misses: number;
  coalesced: number;
  invalidations: number;
}>;

type Entry<T> = { promise: Promise<T>; pending: boolean };

function abortError(): Error {
  const error = new Error("The inference request was aborted");
  error.name = "AbortError";
  return error;
}

function callerView<T>(promise: Promise<T>, signal?: AbortSignal): Promise<T> {
  if (!signal) return promise;
  if (signal.aborted) return Promise.reject(abortError());
  return new Promise<T>((resolve, reject) => {
    const abort = () => reject(abortError());
    signal.addEventListener("abort", abort, { once: true });
    promise.then(resolve, reject).finally(() => signal.removeEventListener("abort", abort));
  });
}

export class InferenceRequestCache {
  private entries = new Map<string, Entry<unknown>>();
  private counters = { hits: 0, misses: 0, coalesced: 0, invalidations: 0 };

  get<T>(key: string, load: () => Promise<T>, signal?: AbortSignal): Promise<T> {
    const current = this.entries.get(key) as Entry<T> | undefined;
    if (current) {
      this.counters.hits += 1;
      if (current.pending) this.counters.coalesced += 1;
      return callerView(current.promise, signal);
    }
    this.counters.misses += 1;
    const entry: Entry<T> = { pending: true, promise: Promise.resolve().then(load) };
    this.entries.set(key, entry);
    entry.promise = entry.promise.then(
      (value) => {
        entry.pending = false;
        return value;
      },
      (error) => {
        if (this.entries.get(key) === entry) this.entries.delete(key);
        throw error;
      },
    );
    return callerView(entry.promise, signal);
  }

  invalidatePrefix(prefix: string): void {
    let removed = 0;
    for (const key of this.entries.keys()) {
      if (key.startsWith(prefix)) {
        this.entries.delete(key);
        removed += 1;
      }
    }
    this.counters.invalidations += removed;
  }

  reset(): void {
    this.entries.clear();
    this.counters = { hits: 0, misses: 0, coalesced: 0, invalidations: 0 };
  }

  stats(): InferenceCacheStats {
    return { ...this.counters };
  }
}

export const inferenceRequestCache = new InferenceRequestCache();

export function candidateInferencePrefix(projectId: string, candidateId = ""): string {
  const projectPrefix = `${encodeURIComponent(projectId)}::`;
  return candidateId ? `${projectPrefix}${encodeURIComponent(candidateId)}::` : projectPrefix;
}

export function inferenceRequestKey(
  projectId: string,
  candidateId: string,
  inputIdentity: string,
  operation: "preview" | "curve",
  parameter = "",
): string {
  return `${candidateInferencePrefix(projectId, candidateId)}${inputIdentity}::${operation}::${parameter}`;
}

export function candidateInputIdentity(inputs: unknown): string {
  const canonicalize = (value: unknown): unknown => {
    if (Array.isArray(value)) return value.map(canonicalize);
    if (typeof value !== "object" || value === null) return value;
    return Object.fromEntries(
      Object.entries(value).sort(([left], [right]) => left.localeCompare(right)).map(([key, item]) => [key, canonicalize(item)]),
    );
  };
  return JSON.stringify(canonicalize(inputs)) ?? "undefined";
}

declare global {
  interface Window {
    __materialInferenceCacheStats?: () => InferenceCacheStats;
  }
}

if (typeof window !== "undefined") {
  window.__materialInferenceCacheStats = () => inferenceRequestCache.stats();
}
