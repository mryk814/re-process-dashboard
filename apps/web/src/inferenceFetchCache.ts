type JsonRecord = Record<string, unknown>;

type CacheStats = {
  hits: number;
  misses: number;
  coalesced: number;
  invalidations: number;
};

type CacheEntry = {
  response: Response;
};

const INFERENCE_PATH = /\/api\/candidates\/([^/]+)\/(preview|response-curve|response-curves)$/;
const CANDIDATE_PATH = /\/api\/candidates\/([^/?]+)$/;
const CANDIDATE_LIST_PATH = /\/api\/candidates$/;

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

export function candidateInferenceIdentity(candidate: JsonRecord): string {
  const canonical = {
    schema_version: candidate.schema_version,
    task_id: candidate.task_id,
    composition: candidate.composition,
    process: candidate.process,
    categorical: candidate.categorical,
    heat_pattern: candidate.heat_pattern,
    thickness_mm: candidate.thickness_mm,
    line_speed_m_min: candidate.line_speed_m_min,
    coating: candidate.coating,
  };
  return JSON.stringify(stableValue(canonical));
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

function withoutSignal(input: RequestInfo | URL, init?: RequestInit): [RequestInfo | URL, RequestInit | undefined] {
  const nextInit = init ? { ...init } : undefined;
  if (nextInit) delete nextInit.signal;
  if (input instanceof Request) {
    return [new Request(input, { signal: undefined }), nextInit];
  }
  return [input, nextInit];
}

function abortError(): DOMException {
  return new DOMException("The operation was aborted", "AbortError");
}

async function withCallerAbort<T>(promise: Promise<T>, signal?: AbortSignal | null): Promise<T> {
  if (!signal) return promise;
  if (signal.aborted) throw abortError();
  return await Promise.race([
    promise,
    new Promise<T>((_, reject) => {
      signal.addEventListener("abort", () => reject(abortError()), { once: true });
    }),
  ]);
}

export function installInferenceFetchCache(): void {
  if (window.__materialInferenceCacheInstalled) return;
  window.__materialInferenceCacheInstalled = true;

  const originalFetch = window.fetch.bind(window);
  const cache = new Map<string, CacheEntry>();
  const inFlight = new Map<string, Promise<Response>>();
  const candidateIdentities = new Map<string, string>();
  const stats: CacheStats = { hits: 0, misses: 0, coalesced: 0, invalidations: 0 };

  function invalidateCandidate(candidateId: string): void {
    let changed = false;
    for (const key of cache.keys()) {
      const url = new URL(key);
      const match = url.pathname.match(INFERENCE_PATH);
      if (match?.[1] === candidateId) {
        cache.delete(key);
        changed = true;
      }
    }
    if (changed) stats.invalidations += 1;
  }

  async function rememberCandidate(candidate: JsonRecord): Promise<void> {
    const id = candidate.id;
    if (typeof id === "string" && id) {
      candidateIdentities.set(id, candidateInferenceIdentity(candidate));
    }
  }

  async function rememberCandidates(response: Response): Promise<void> {
    try {
      const payload = (await response.clone().json()) as unknown;
      if (Array.isArray(payload)) {
        await Promise.all(payload.filter((item): item is JsonRecord => Boolean(item && typeof item === "object")).map(rememberCandidate));
      } else if (payload && typeof payload === "object") {
        await rememberCandidate(payload as JsonRecord);
      }
    } catch {
      // Non-JSON responses are irrelevant to inference invalidation.
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
    const inferenceMatch = url.pathname.match(INFERENCE_PATH);

    if (inferenceMatch && ((inferenceMatch[2] === "preview" && method === "POST") || method === "GET")) {
      const key = url.toString();
      const cached = cache.get(key);
      if (cached) {
        stats.hits += 1;
        return cached.response.clone();
      }

      const existing = inFlight.get(key);
      if (existing) {
        stats.coalesced += 1;
        return await withCallerAbort(existing.then((response) => response.clone()), init?.signal ?? (input instanceof Request ? input.signal : undefined));
      }

      stats.misses += 1;
      const [upstreamInput, upstreamInit] = withoutSignal(input, init);
      const upstream = originalFetch(upstreamInput, upstreamInit)
        .then((response) => {
          if (response.ok) cache.set(key, { response: response.clone() });
          return response;
        })
        .finally(() => inFlight.delete(key));
      inFlight.set(key, upstream);
      return await withCallerAbort(upstream.then((response) => response.clone()), init?.signal ?? (input instanceof Request ? input.signal : undefined));
    }

    const candidateMatch = url.pathname.match(CANDIDATE_PATH);
    const listMatch = url.pathname.match(CANDIDATE_LIST_PATH);
    const beforeIdentity = candidateMatch ? candidateIdentities.get(candidateMatch[1]) : undefined;
    const requestCandidate = candidateMatch && method === "PUT" ? await requestJson(input, init) : null;
    const response = await originalFetch(input, init);

    if (response.ok && listMatch && method === "GET") {
      await rememberCandidates(response);
    } else if (response.ok && listMatch && method === "POST") {
      await rememberCandidates(response);
    } else if (candidateMatch && response.ok && method === "PUT") {
      const candidateId = candidateMatch[1];
      const afterIdentity = requestCandidate ? candidateInferenceIdentity(requestCandidate) : undefined;
      if (afterIdentity && afterIdentity !== beforeIdentity) invalidateCandidate(candidateId);
      if (afterIdentity) candidateIdentities.set(candidateId, afterIdentity);
    } else if (candidateMatch && method === "DELETE" && response.ok) {
      invalidateCandidate(candidateMatch[1]);
      candidateIdentities.delete(candidateMatch[1]);
    }

    return response;
  };
}

declare global {
  interface Window {
    __materialInferenceCacheInstalled?: boolean;
    __materialInferenceCacheStats?: CacheStats;
    __materialInferenceCacheReset?: () => void;
  }
}
