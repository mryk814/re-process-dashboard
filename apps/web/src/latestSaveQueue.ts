export type QueuedSave<T> = {
  promise: Promise<T>;
  isLatest: () => boolean;
  release: () => void;
};

/** Serializes saves per entity and lets callers ignore responses superseded by a newer draft. */
export class LatestSaveQueue<T> {
  private readonly sequences = new Map<string, number>();
  private readonly chains = new Map<string, Promise<T>>();

  enqueue(
    key: string,
    initial: T,
    save: (current: T) => Promise<T>,
    recover?: (error: unknown) => T | Promise<T>,
  ): QueuedSave<T> {
    const sequence = (this.sequences.get(key) ?? 0) + 1;
    this.sequences.set(key, sequence);
    const previous = this.chains.get(key);
    const prior = previous
      ? previous.catch((error: unknown) => recover ? recover(error) : Promise.reject(error))
      : Promise.resolve(initial);
    const promise = prior.then(save);
    this.chains.set(key, promise);
    return {
      promise,
      isLatest: () => this.sequences.get(key) === sequence,
      release: () => {
        if (this.sequences.get(key) === sequence) this.chains.delete(key);
      },
    };
  }
}

function same(left: unknown, right: unknown): boolean {
  return JSON.stringify(left) === JSON.stringify(right);
}

/** Applies only fields changed by the draft, preserving unrelated authoritative changes. */
export function rebaseChangedFields<T>(base: T, draft: T, current: T): T {
  if (same(base, draft)) return current;
  if (
    base === null || draft === null || current === null
    || typeof base !== "object" || typeof draft !== "object" || typeof current !== "object"
    || Array.isArray(base) || Array.isArray(draft) || Array.isArray(current)
  ) return draft;
  const result = { ...(current as Record<string, unknown>) };
  for (const key of new Set([...Object.keys(base), ...Object.keys(draft)])) {
    result[key] = rebaseChangedFields(
      (base as Record<string, unknown>)[key],
      (draft as Record<string, unknown>)[key],
      (current as Record<string, unknown>)[key],
    );
  }
  return result as T;
}
