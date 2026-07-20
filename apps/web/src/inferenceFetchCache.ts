type JsonRecord = Record<string, unknown>;

type InferenceCacheStats = {
  hits: number;
  misses: number;
  coalesced: number;
  invalidations: number;
};

const PROJECT_PREVIEW_PATH = /\/api\/projects\/([^/]+)\/candidates\/([^/]+)\/preview$/;
const PROJECT_PATH = /\/api\/projects\/([^/]+)$/;
const DEFAULT_PROJECT_PATH = /\/api\/project$/;
const CURVE_PATH = /\/api\/candidates\/([^/]+)\/(response-curve|response-curves)$/;
const ANNEALED_CANDIDATE_PATH = /\/api\/candidates\/([^/?]+)$/;
const HOT_ROLLING_CANDIDATE_PATH = /\/api\/hot-rolling\/candidates\/([^/?]+)$/;
const ANNEALED_CANDIDATE_LIST_PATH = /\/api\/candidates$/;
const HOT_ROLLING_CANDIDATE_LIST_PATH = /\/api\/hot-rolling\/candidates$/;

function stableValue(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(stableValue);
  if (value && typeof value === "object") {
    return Object.fromEntries(
      Object.entries(value as JsonRecord)
        .sort(([left], [right]) => left.localeCompare(right))
        .map(([key, item]) => [key, stableValue(item)]),
    );
  }
  return value;
}

/**
 * Candidate identity for inference invalidation.
 * Display and persistence metadata are deliberately excluded. Project-level
 * target values are invalidated separately when the project is updated.
 */
export function candidateInferenceIdentity(candidate: JsonRecord): string {
  const {
    id: _id,
    project_id: _projectId,
    name: _name,
    created_at: _createdAt,
    updated_at: _updatedAt,
    revision: _revision,
    ...inferenceInputs
  } = candidate;
  return JSON.stringify(stableValue(inferenceInputs));
}

function requestUrl(input: RequestInfo | URL): URL {
  const raw = input instanceof Request ? input.url : String(input);
  return new URL(raw, window.location.href);
}

function requestMethod(input: RequestInfo | URL, init?: RequestInit): string {
  return (init?.method ?? (input instanceof Request ? input.method : "GET")).toUpperCase();
}

async function requestJson(input: RequestInfo | URL, init?: RequestInit): Promise<JsonRecord | null> {
  try {
    if (typeof init?.body === "string") return JSON.parse(init.body) as JsonRecord;
    if (input instanceof Request) return (await input.clone().json()) as JsonRecord;
  } catch {
    return null;
  }
  return null;
}

function withoutCallerSignal(
  input: RequestInfo | URL,
  init?: RequestInit,
): [RequestInfo | URL, RequestInit | undefined] {
  const nextInit = init ? { ...init, signal: null } : undefined;
  if (input instanceof Request) return [new Request(input, { signal: null }), nextInit];
  return [input, nextInit];
}

function abortError(): DOMException {
  return new DOMException("The operation was aborted", "AbortError");
}

async function withCallerAbort<T>(promise: Promise<T>, signal?: AbortSignal | null): Promise<T> {
  if (!signal) return promise;
  if (signal.aborted) throw abortError();

  return await new Promise<T>((resolve, reject) => {
    const onAbort = () => reject(abortError());
    signal.addEventListener("abort", onAbort, { once: true });
    promise.then(
      (value) => {
        signal.removeEventListener("abort", onAbort);
        resolve(value);
      },
      (error) => {
        signal.removeEventListener("abort", onAbort);
        reject(error);
      },
    );
  });
}

function inferenceCandidateId(url: URL): string | null {
  return url.pathname.match(PROJECT_PREVIEW_PATH)?.[2]
    ?? url.pathname.match(CURVE_PATH)?.[1]
    ?? null;
}

function previewProjectId(url: URL): string | null {
  return url.pathname.match(PROJECT_PREVIEW_PATH)?.[1] ?? null;
}

function mutationProjectId(url: URL): string | null {
  return url.pathname.match(PROJECT_PATH)?.[1]
    ?? (DEFAULT_PROJECT_PATH.test(url.pathname) ? "default" : null);
}

function mutationCandidateId(url: URL): string | null {
  return url.pathname.match(ANNEALED_CANDIDATE_PATH)?.[1]
    ?? url.pathname.match(HOT_ROLLING_CANDIDATE_PATH)?.[1]
    ?? null;
}

function isCandidateList(url: URL): boolean {
  return ANNEALED_CANDIDATE_LIST_PATH.test(url.pathname)
    || HOT_ROLLING_CANDIDATE_LIST_PATH.test(url.pathname);
}

function isInferenceRequest(url: URL, method: string): boolean {
  return (method === "POST" && PROJECT_PREVIEW_PATH.test(url.pathname))
    || (method === "GET" && CURVE_PATH.test(url.pathname));
}

export function installInferenceFetchCache(): void {
  if (window.__materialInferenceCacheInstalled) return;
  window.__materialInferenceCacheInstalled = true;

  const originalFetch = window.fetch.bind(window);
  const cache = new Map<string, Response>();
  const inFlight = new Map<string, Promise<Response>>();
  const candidateIdentities = new Map<string, string>();
  const stats: InferenceCacheStats = { hits: 0, misses: 0, coalesced: 0, invalidations: 0 };

  function cacheUrl(key: string): URL {
    const separator = key.indexOf(" ");
    return new URL(key.slice(separator + 1));
  }

  function invalidateMatching(predicate: (url: URL) => boolean): void {
    let changed = false;
    for (const key of [...cache.keys()]) {
      if (predicate(cacheUrl(key))) {
        cache.delete(key);
        changed = true;
      }
    }
    if (changed) stats.invalidations += 1;
  }

  function invalidateCandidate(candidateId: string): void {
    invalidateMatching((url) => inferenceCandidateId(url) === candidateId);
  }

  function invalidateProject(projectId: string): void {
    invalidateMatching((url) => previewProjectId(url) === projectId);
  }

  function rememberCandidate(candidate: JsonRecord): void {
    if (typeof candidate.id === "string" && candidate.id) {
      candidateIdentities.set(candidate.id, candidateInferenceIdentity(candidate));
    }
  }

  async function rememberCandidates(response: Response): Promise<void> {
    try {
      const payload = (await response.clone().json()) as unknown;
      if (Array.isArray(payload)) {
        for (const item of payload) {
          if (item && typeof item === "object") rememberCandidate(item as JsonRecord);
        }
      } else if (payload && typeof payload === "object") {
        rememberCandidate(payload as JsonRecord);
      }
    } catch {
      // Candidate identity tracking is best-effort; response handling must not change.
    }
  }

  window.__materialInferenceCacheStats = stats;
  window.__materialInferenceCacheReset = () => {
    cache.clear();
    inFlight.clear();
    candidateIdentities.clear();
    stats.hits = 0;
    stats.misses = 0;
    stats.coalesced = 0;
    stats.invalidations = 0;
  };

  window.fetch = async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
    const url = requestUrl(input);
    const method = requestMethod(input, init);
    const callerSignal = init?.signal ?? (input instanceof Request ? input.signal : undefined);

    if (isInferenceRequest(url, method)) {
      const key = `${method} ${url.toString()}`;
      const cached = cache.get(key);
      if (cached) {
        stats.hits += 1;
        return cached.clone();
      }

      const existing = inFlight.get(key);
      if (existing) {
        stats.coalesced += 1;
        return await withCallerAbort(existing.then((response) => response.clone()), callerSignal);
      }

      stats.misses += 1;
      const [upstreamInput, upstreamInit] = withoutCallerSignal(input, init);
      const upstream = originalFetch(upstreamInput, upstreamInit)
        .then((response) => {
          if (response.ok) cache.set(key, response.clone());
          return response;
        })
        .finally(() => inFlight.delete(key));
      inFlight.set(key, upstream);
      return await withCallerAbort(upstream.then((response) => response.clone()), callerSignal);
    }

    const candidateId = mutationCandidateId(url);
    const projectId = method === "PUT" ? mutationProjectId(url) : null;
    const previousIdentity = candidateId ? candidateIdentities.get(candidateId) : undefined;
    const requestCandidate = candidateId && method === "PUT" ? await requestJson(input, init) : null;
    const response = await originalFetch(input, init);

    if (response.ok && isCandidateList(url) && (method === "GET" || method === "POST")) {
      await rememberCandidates(response);
    } else if (response.ok && candidateId && method === "PUT") {
      const nextIdentity = requestCandidate ? candidateInferenceIdentity(requestCandidate) : undefined;
      if (!previousIdentity || !nextIdentity || previousIdentity !== nextIdentity) {
        invalidateCandidate(candidateId);
      }
      if (nextIdentity) candidateIdentities.set(candidateId, nextIdentity);
    } else if (response.ok && candidateId && method === "DELETE") {
      invalidateCandidate(candidateId);
      candidateIdentities.delete(candidateId);
    } else if (response.ok && projectId) {
      invalidateProject(projectId);
    }

    return response;
  };
}

declare global {
  interface Window {
    __materialInferenceCacheInstalled?: boolean;
    __materialInferenceCacheStats?: InferenceCacheStats;
    __materialInferenceCacheReset?: () => void;
  }
}
