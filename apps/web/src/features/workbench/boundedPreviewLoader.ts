export type PreviewLoadItem = Readonly<{
  id: string;
}>;

type Options<TItem extends PreviewLoadItem, TResult> = Readonly<{
  items: readonly TItem[];
  selectedId: string;
  load: (item: TItem) => Promise<TResult | null>;
  onSelectedLoaded?: (item: TItem, result: TResult) => void | Promise<void>;
  concurrency?: number;
  signal?: AbortSignal;
}>;

/**
 * Loads the selected candidate before starting background preview work, then
 * bounds the remaining fan-out. Results retain selected-first input order.
 */
export async function loadSelectedFirstBounded<TItem extends PreviewLoadItem, TResult>({
  items,
  selectedId,
  load,
  onSelectedLoaded,
  concurrency = 2,
  signal,
}: Options<TItem, TResult>): Promise<Array<readonly [string, TResult]>> {
  if (!Number.isInteger(concurrency) || concurrency < 1) throw new Error("concurrency must be a positive integer");
  const selected = items.find((item) => item.id === selectedId);
  const remaining = items.filter((item) => item.id !== selectedId);
  const ordered = selected ? [selected, ...remaining] : [...items];
  const results: Array<TResult | null | undefined> = new Array(ordered.length);

  if (selected && !signal?.aborted) {
    results[0] = await load(selected);
    if (results[0] !== null && results[0] !== undefined && !signal?.aborted) {
      await onSelectedLoaded?.(selected, results[0]);
    }
  }
  if (signal?.aborted) return [];

  const startIndex = selected ? 1 : 0;
  let cursor = startIndex;
  async function worker() {
    while (!signal?.aborted) {
      const index = cursor;
      cursor += 1;
      if (index >= ordered.length) return;
      results[index] = await load(ordered[index]);
    }
  }
  await Promise.all(Array.from(
    { length: Math.min(concurrency, Math.max(0, ordered.length - startIndex)) },
    () => worker(),
  ));

  if (signal?.aborted) return [];
  return ordered.flatMap((item, index) => {
    const value = results[index];
    return value === null || value === undefined ? [] : [[item.id, value] as const];
  });
}
