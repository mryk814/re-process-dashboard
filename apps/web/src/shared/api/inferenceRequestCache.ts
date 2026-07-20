export type InferenceCacheStats = Readonly<{
  hits: number;
  misses: number;
  coalesced: number;
  invalidations: number;
}>;

type Entry<T> = {
  promise: Promise<T>;
  pending: boolean;
  controller: AbortController;
  subscribers: Set<symbol>;
};

function abortError(): Error {
  const error = new Error("The inference request was aborted");
  error.name = "AbortError";
  return error;
}

function callerView<T>(entry: Entry<T>, onOrphaned: () => void, signal?: AbortSignal): Promise<T> {
  if (signal?.aborted) return Promise.reject(abortError());
  const subscriber = Symbol("inference-subscriber");
  entry.subscribers.add(subscriber);
  return new Promise<T>((resolve, reject) => {
    const detach = () => {
      if (!entry.subscribers.delete(subscriber)) return;
      if (entry.pending && entry.subscribers.size === 0) onOrphaned();
    };
    const abort = () => {
      detach();
      reject(abortError());
    };
    signal?.addEventListener("abort", abort, { once: true });
    entry.promise.then((value) => {
      signal?.removeEventListener("abort", abort);
      detach();
      resolve(value);
    }, (error) => {
      signal?.removeEventListener("abort", abort);
      detach();
      reject(error);
    });
  });
}

export class InferenceRequestCache {
  private entries = new Map<string, Entry<unknown>>();
  private counters = { hits: 0, misses: 0, coalesced: 0, invalidations: 0 };

  get<T>(key: string, load: (signal: AbortSignal) => Promise<T>, signal?: AbortSignal): Promise<T> {
    if (signal?.aborted) return Promise.reject(abortError());
    const current = this.entries.get(key) as Entry<T> | undefined;
    if (current) {
      this.counters.hits += 1;
      if (current.pending) this.counters.coalesced += 1;
      return callerView(current, () => {
        if (this.entries.get(key) === current) this.entries.delete(key);
        current.controller.abort();
      }, signal);
    }
    this.counters.misses += 1;
    const controller = new AbortController();
    const entry: Entry<T> = {
      pending: true,
      controller,
      subscribers: new Set(),
      promise: Promise.resolve().then(() => load(controller.signal)),
    };
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
    return callerView(entry, () => {
      if (this.entries.get(key) === entry) this.entries.delete(key);
      entry.controller.abort();
    }, signal);
  }

  invalidatePrefix(prefix: string): void {
    let removed = 0;
    for (const key of this.entries.keys()) {
      if (key.startsWith(prefix)) {
        const entry = this.entries.get(key);
        this.entries.delete(key);
        if (entry?.pending) entry.controller.abort();
        removed += 1;
      }
    }
    this.counters.invalidations += removed;
  }

  reset(): void {
    for (const entry of this.entries.values()) {
      if (entry.pending) entry.controller.abort();
    }
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

export function candidateInferenceChanged(previousInputs: unknown, nextInputs: unknown): boolean {
  return candidateInputIdentity(previousInputs) !== candidateInputIdentity(nextInputs);
}

export function shouldRefreshPreviewAfterSave(
  baseInputIdentity: string,
  savedInputIdentity: string,
  previewInputIdentity?: string,
): boolean {
  return savedInputIdentity !== baseInputIdentity
    || (previewInputIdentity !== undefined && savedInputIdentity !== previewInputIdentity);
}

export function mergePreviewEntryIfCurrent<T extends { canonical_input: unknown }>(
  current: Record<string, T>,
  candidateId: string,
  expectedInputIdentity: string,
  currentInputIdentity: string | undefined,
  existingInputIdentity: string | undefined,
  preview: T,
): Record<string, T> {
  if (currentInputIdentity !== expectedInputIdentity) return current;
  const existing = current[candidateId];
  if (existing && existingInputIdentity !== expectedInputIdentity) return current;
  return { ...current, [candidateId]: preview };
}

declare global {
  interface Window {
    __materialInferenceCacheStats?: () => InferenceCacheStats;
  }
}

if (typeof window !== "undefined") {
  window.__materialInferenceCacheStats = () => inferenceRequestCache.stats();
}
